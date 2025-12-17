
import requests
from celery import shared_task
from accounts.models import GHLAuthCredentials
from decouple import config
from accounts.utils import (
    fetch_all_contacts,
    create_or_update_contact,
    delete_contact,
    create_or_update_user_from_ghl,
    create_or_update_appointment_from_ghl
)
from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from service_app.models import Appointment, User, User


@shared_task
def make_api_call():
    credentials = GHLAuthCredentials.objects.first()
    
    print("credentials tokenL", credentials)
    refresh_token = credentials.refresh_token

    
    response = requests.post('https://services.leadconnectorhq.com/oauth/token', data={
        'grant_type': 'refresh_token',
        'client_id': config("GHL_CLIENT_ID"),
        'client_secret': config("GHL_CLIENT_SECRET"),
        'refresh_token': refresh_token
    })
    
    new_tokens = response.json()

    print("new tokens: ", new_tokens)

    obj, created = GHLAuthCredentials.objects.update_or_create(
            location_id= new_tokens.get("locationId"),
            defaults={
                "access_token": new_tokens.get("access_token"),
                "refresh_token": new_tokens.get("refresh_token"),
                "expires_in": new_tokens.get("expires_in"),
                "scope": new_tokens.get("scope"),
                "user_type": new_tokens.get("userType"),
                "company_id": new_tokens.get("companyId"),
                "user_id":new_tokens.get("userId"),

            }
        )
    


@shared_task
def fetch_all_contacts_task(location_id, access_token):
    """
    Celery task to fetch all contacts for a given location using the provided access token.
    """
    fetch_all_contacts(location_id, access_token)


@shared_task
def handle_webhook_event(data, event_type):
    try:
        if event_type in ["ContactCreate", "ContactUpdate"]:
            create_or_update_contact(data)
        elif event_type == "ContactDelete":
            delete_contact(data)
        elif event_type == "UserCreate":
            # Handle user creation from GHL
            create_or_update_user_from_ghl(data)
        elif event_type in ["AppointmentCreate"]:
            # Handle appointment creation/update from GHL
            location_id = data.get("locationId")
            create_or_update_appointment_from_ghl(data, location_id)
    except Exception as e:
        print(f"Error handling webhook event: {str(e)}")


@shared_task
def fetch_and_save_all_appointments(location_id=None):
    """
    Fetch all appointments from GHL API for all users (using their ghl_user_id)
    for the last 1 year to 2 years in the future and save them to the Appointment table.
    
    Args:
        location_id (str, optional): Location ID. If not provided, will use from credentials.
    
    Returns:
        dict: Summary with counts of created and updated appointments
    """
    from django.db import transaction
    from accounts.models import Contact
    
    try:
        # Get credentials
        credentials = GHLAuthCredentials.objects.first()
        if not credentials:
            raise ValueError("No GHLAuthCredentials found in database")
        
        # Use location_id from credentials if not provided
        if not location_id:
            location_id = credentials.location_id
        
        if not location_id:
            raise ValueError("location_id is required")
        
        access_token = credentials.access_token
        
        # Fetch all users with ghl_user_id
        users = User.objects.filter(ghl_user_id__isnull=False).exclude(ghl_user_id='')
        for user in users:
            print(f"User: {user.ghl_user_id}, {user.first_name} {user.last_name}")
        user_ghl_ids = list(users.values_list('ghl_user_id', flat=True))
        
        if not user_ghl_ids:
            print("No users with ghl_user_id found. Skipping appointment fetch.")
            return {
                "created": 0,
                "updated": 0,
                "total": 0
            }
        
        print(f"Found {len(user_ghl_ids)} users with ghl_user_id. Fetching appointments for each...")
        
        # Calculate time range: 1 year ago to 2 years in the future
        now = timezone.now()
        start_time = now - timedelta(days=365)  # 1 year ago
        end_time = now + timedelta(days=10000)    # 2 years in the future
        
        # Convert to milliseconds timestamp
        start_time_ms = int(start_time.timestamp() * 1000)
        end_time_ms = int(end_time.timestamp() * 1000)
        
        print(f"Fetching appointments from {start_time} to {end_time}")
        print(f"Timestamps: {start_time_ms} to {end_time_ms}")
        
        # API endpoint
        url = "https://services.leadconnectorhq.com/calendars/events"
        
        headers = {
            "Accept": "application/json",
            "Version": "2021-04-15",
            "Authorization": f"Bearer {access_token}"
        }
        
        # Collect all events from all users
        all_events = []
        
        # Loop through each user's ghl_user_id
        for user_ghl_id in user_ghl_ids:
            print(f"Fetching appointments for user: {user_ghl_id}")
            
            params = {
                "locationId": location_id,
                "userId": user_ghl_id,
                "startTime": start_time_ms,
                "endTime": end_time_ms
            }
            
            # Make API request
            try:
                response = requests.get(url, headers=headers, params=params)
                
                if response.status_code != 200:
                    print(f"Error Response for user {user_ghl_id}: {response.status_code}")
                    print(f"Error Details: {response.text}")
                    continue  # Skip this user and continue with next
                
                data = response.json()
                events = data.get("events", [])
                all_events.extend(events)
                print(f"Fetched {len(events)} appointments for user {user_ghl_id}")
                
            except Exception as e:
                print(f"Error fetching appointments for user {user_ghl_id}: {str(e)}")
                continue  # Skip this user and continue with next
        
        print(f"Total fetched {len(all_events)} appointments from GHL API across all users")
        
        if not all_events:
            return {
                "created": 0,
                "updated": 0,
                "total": 0
            }
        
        # Parse all events into Appointment objects
        appointment_objects = []
        appointment_data_map = {}  # Store event data for relationship linking
        
        for event in all_events:
            try:
                ghl_appointment_id = event.get("id")
                if not ghl_appointment_id:
                    print(f"Skipping event without ID: {event.get('title', 'Unknown')}")
                    continue
                
                # Handle nested structure
                event_data = event
                if "appointment" in event:
                    event_data = event["appointment"]
                
                # Parse datetime fields
                start_time_dt = parse_datetime(event_data.get("startTime")) if event_data.get("startTime") else None
                end_time_dt = parse_datetime(event_data.get("endTime")) if event_data.get("endTime") else None
                date_added_dt = parse_datetime(event_data.get("dateAdded")) if event_data.get("dateAdded") else None
                date_updated_dt = parse_datetime(event_data.get("dateUpdated")) if event_data.get("dateUpdated") else None
                
                # Create Appointment object
                appointment = Appointment(
                    ghl_appointment_id=ghl_appointment_id,
                    location_id=location_id or event_data.get("locationId", ""),
                    title=event_data.get("title"),
                    address=event_data.get("address"),
                    calendar_id=event_data.get("calendarId"),
                    appointment_status=event_data.get("appointmentStatus"),
                    source=event_data.get("source"),
                    notes=event_data.get("notes") or event_data.get("description"),
                    ghl_contact_id=event_data.get("contactId"),
                    ghl_assigned_user_id=event_data.get("assignedUserId"),
                    start_time=start_time_dt,
                    end_time=end_time_dt,
                    date_added=date_added_dt,
                    date_updated=date_updated_dt,
                    users_ghl_ids=event_data.get("users", []),
                )
                
                appointment_objects.append(appointment)
                appointment_data_map[ghl_appointment_id] = event_data
                
            except Exception as e:
                print(f"Error parsing appointment {event.get('id', 'Unknown')}: {str(e)}")
                continue
        
        print(f"Parsed {len(appointment_objects)} appointments")
        
        # Get existing appointments in bulk
        ghl_appointment_ids = [appt.ghl_appointment_id for appt in appointment_objects]
        existing_appointments = {
            appt.ghl_appointment_id: appt
            for appt in Appointment.objects.filter(ghl_appointment_id__in=ghl_appointment_ids)
        }
        
        # Separate into create and update lists
        appointments_to_create = []
        appointments_to_update = []
        
        for appointment in appointment_objects:
            if appointment.ghl_appointment_id in existing_appointments:
                # Update existing appointment
                existing = existing_appointments[appointment.ghl_appointment_id]
                # Update all fields
                existing.location_id = appointment.location_id
                existing.title = appointment.title
                existing.address = appointment.address
                existing.calendar_id = appointment.calendar_id
                existing.appointment_status = appointment.appointment_status
                existing.source = appointment.source
                existing.notes = appointment.notes
                existing.ghl_contact_id = appointment.ghl_contact_id
                existing.ghl_assigned_user_id = appointment.ghl_assigned_user_id
                existing.start_time = appointment.start_time
                existing.end_time = appointment.end_time
                existing.date_added = appointment.date_added
                existing.date_updated = appointment.date_updated
                existing.users_ghl_ids = appointment.users_ghl_ids
                appointments_to_update.append(existing)
            else:
                appointments_to_create.append(appointment)
        
        # Bulk operations
        created_count = 0
        updated_count = 0
        
        with transaction.atomic():
            # Bulk create new appointments
            if appointments_to_create:
                Appointment.objects.bulk_create(
                    appointments_to_create,
                    ignore_conflicts=True
                )
                created_count = len(appointments_to_create)
                print(f"Bulk created {created_count} appointments")
            
            # Bulk update existing appointments
            if appointments_to_update:
                Appointment.objects.bulk_update(
                    appointments_to_update,
                    fields=[
                        'location_id', 'title', 'address', 'calendar_id',
                        'appointment_status', 'source', 'notes', 'ghl_contact_id',
                        'ghl_assigned_user_id', 'start_time', 'end_time',
                        'date_added', 'date_updated', 'users_ghl_ids'
                    ]
                )
                updated_count = len(appointments_to_update)
                print(f"Bulk updated {updated_count} appointments")
        
        # Handle relationships in bulk after main operations
        # Get all appointments again for relationship linking
        all_appointment_ids = ghl_appointment_ids
        appointments_dict = {
            appt.ghl_appointment_id: appt
            for appt in Appointment.objects.filter(ghl_appointment_id__in=all_appointment_ids)
        }
        
        # Get all contacts and users in bulk
        contact_ids = set()
        user_ids = set()
        for event_data in appointment_data_map.values():
            if event_data.get("contactId"):
                contact_ids.add(event_data.get("contactId"))
            if event_data.get("assignedUserId"):
                user_ids.add(event_data.get("assignedUserId"))
            if event_data.get("users"):
                user_ids.update(event_data.get("users"))
        
        # Fetch contacts and users in bulk
        contacts_dict = {
            contact.contact_id: contact
            for contact in Contact.objects.filter(contact_id__in=contact_ids)
        } if contact_ids else {}
        
        users_dict = {
            user.ghl_user_id: user
            for user in User.objects.filter(ghl_user_id__in=user_ids)
        } if user_ids else {}
        
        # Link relationships
        appointments_to_update_relationships = []
        many_to_many_updates = {}  # appointment_id -> list of user objects
        
        for ghl_appointment_id, event_data in appointment_data_map.items():
            if ghl_appointment_id not in appointments_dict:
                continue
            
            appointment = appointments_dict[ghl_appointment_id]
            needs_update = False
            
            # Link contact
            contact_id = event_data.get("contactId")
            if contact_id and contact_id in contacts_dict:
                if appointment.contact_id != contacts_dict[contact_id].id:
                    appointment.contact = contacts_dict[contact_id]
                    needs_update = True
            elif appointment.contact_id is not None:
                appointment.contact = None
                needs_update = True
            
            # Link assigned user
            assigned_user_id = event_data.get("assignedUserId")
            if assigned_user_id and assigned_user_id in users_dict:
                if appointment.assigned_user_id != users_dict[assigned_user_id].id:
                    appointment.assigned_user = users_dict[assigned_user_id]
                    needs_update = True
            elif appointment.assigned_user_id is not None:
                appointment.assigned_user = None
                needs_update = True
            
            # Prepare users for many-to-many
            users_ghl_ids = event_data.get("users", [])
            if users_ghl_ids:
                users_to_add = [
                    users_dict[uid] for uid in users_ghl_ids
                    if uid in users_dict
                ]
                many_to_many_updates[appointment.id] = users_to_add
            
            if needs_update:
                appointments_to_update_relationships.append(appointment)
        
        # Bulk update relationships
        if appointments_to_update_relationships:
            with transaction.atomic():
                Appointment.objects.bulk_update(
                    appointments_to_update_relationships,
                    fields=['contact', 'assigned_user']
                )
                print(f"Updated relationships for {len(appointments_to_update_relationships)} appointments")
        
        # Handle many-to-many relationships (users)
        if many_to_many_updates:
            for appointment_id, users_list in many_to_many_updates.items():
                try:
                    appointment = Appointment.objects.get(id=appointment_id)
                    appointment.users.clear()
                    if users_list:
                        appointment.users.add(*users_list)
                except Appointment.DoesNotExist:
                    continue
            print(f"Updated many-to-many relationships for {len(many_to_many_updates)} appointments")
        
        # Delete appointments that exist in our app but not in GHL
        # Only delete appointments that:
        # 1. Have a ghl_appointment_id (are synced from GHL)
        # 2. Are within the time range we're syncing
        # 3. Are not in the fetched GHL appointments list
        deleted_count = 0
        if ghl_appointment_ids:
            # Get set of fetched GHL appointment IDs for quick lookup
            fetched_ghl_ids = set(ghl_appointment_ids)
            
            # Find appointments to delete:
            # - Have ghl_appointment_id (synced from GHL)
            # - Within the time range we're syncing
            # - Not in the fetched GHL appointments
            appointments_to_delete = Appointment.objects.filter(
                ghl_appointment_id__isnull=False
            ).exclude(
                ghl_appointment_id__in=fetched_ghl_ids
            ).filter(
                start_time__gte=start_time,
                start_time__lte=end_time
            )
            
            deleted_count = appointments_to_delete.count()
            if deleted_count > 0:
                with transaction.atomic():
                    appointments_to_delete.delete()
                    print(f"Deleted {deleted_count} appointments that no longer exist in GHL")
        
        print(f"Appointment sync completed: {created_count} created, {updated_count} updated, {deleted_count} deleted")
        
        return {
            "created": created_count,
            "updated": updated_count,
            "deleted": deleted_count,
            "total": len(all_events)
        }
        
    except Exception as e:
        print(f"Error fetching appointments: {str(e)}")
        raise