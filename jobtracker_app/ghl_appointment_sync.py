"""
GHL Appointment Sync Utilities
Handles syncing appointments with GoHighLevel API
"""
import requests
from typing import Dict, Any, Optional
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from accounts.models import GHLAuthCredentials, Calendar
from service_app.models import Appointment, User


def get_ghl_headers(access_token: str) -> Dict[str, str]:
    """Get headers for GHL API requests"""
    return {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Version': '2021-04-15',
        'Authorization': f'Bearer {access_token}'
    }


def get_ghl_credentials() -> Optional[GHLAuthCredentials]:
    """Get GHL credentials from database"""
    return GHLAuthCredentials.objects.first()


def format_datetime_for_ghl(dt) -> Optional[str]:
    """Format datetime to GHL API format (ISO 8601 with timezone)"""
    if not dt:
        return None
    if isinstance(dt, str):
        return dt
    # Convert to ISO format with timezone
    return dt.isoformat()


def map_appointment_status_to_ghl(status: Optional[str]) -> Optional[str]:
    """Map our appointment status to GHL status"""
    if not status:
        return None
    
    # GHL uses the same status values
    status_mapping = {
        'new': 'new',
        'confirmed': 'confirmed',
        'cancelled': 'cancelled',
        'showed': 'showed',
        'noshow': 'noshow',
        'invalid': 'invalid',
    }
    return status_mapping.get(status, status)


def get_assigned_user_ghl_id(appointment: Appointment) -> Optional[str]:
    """Get GHL user ID from assigned user"""
    if appointment.assigned_user:
        return appointment.assigned_user.ghl_user_id
    elif appointment.ghl_assigned_user_id:
        return appointment.ghl_assigned_user_id
    return None


def create_appointment_in_ghl(appointment: Appointment) -> Optional[str]:
    """
    Create appointment in GHL and return the GHL appointment ID
    
    Args:
        appointment: Appointment instance to create in GHL
        
    Returns:
        GHL appointment ID if successful, None otherwise
    """
    credentials = get_ghl_credentials()
    if not credentials:
        print("❌ No GHLAuthCredentials found. Cannot sync appointment to GHL.")
        return None
    
    # Skip if this is already a GHL appointment (has ghl_appointment_id that's not local)
    if appointment.ghl_appointment_id and not appointment.ghl_appointment_id.startswith('local_'):
        print(f"⚠️ Appointment {appointment.id} already has GHL ID: {appointment.ghl_appointment_id}")
        return appointment.ghl_appointment_id
    
    if not appointment.start_time or not appointment.end_time:
        print(f"⚠️ Appointment {appointment.id} missing start_time or end_time. Cannot sync to GHL.")
        return None
    
    headers = get_ghl_headers(credentials.access_token)
    url = 'https://services.leadconnectorhq.com/calendars/events/appointments'
    
    # Build payload
    payload = {
        'title': appointment.title or 'Appointment',
        'appointmentStatus': map_appointment_status_to_ghl(appointment.appointment_status),
        'startTime': format_datetime_for_ghl(appointment.start_time),
        'endTime': format_datetime_for_ghl(appointment.end_time),
        'locationId': appointment.location_id or credentials.location_id,
        'ignoreDateRange': False,
        'toNotify': False,
        'ignoreFreeSlotValidation': True,
    }
    
    # Add optional fields
    if appointment.calendar:
        payload['calendarId'] = appointment.calendar.ghl_calendar_id
    
    if appointment.ghl_contact_id:
        payload['contactId'] = appointment.ghl_contact_id
    
    if appointment.address:
        payload['address'] = appointment.address
        payload['meetingLocationType'] = 'custom'
        payload['meetingLocationId'] = 'custom_0'
        payload['overrideLocationConfig'] = True
    
    if appointment.notes:
        payload['description'] = appointment.notes
    
    # Add assigned user
    assigned_user_ghl_id = get_assigned_user_ghl_id(appointment)
    if assigned_user_ghl_id:
        payload['assignedUserId'] = assigned_user_ghl_id
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code in [200, 201]:
            data = response.json()
            # GHL returns appointment ID in different possible fields
            # Check various possible response structures
            ghl_appointment_id = None
            if isinstance(data, dict):
                ghl_appointment_id = (
                    data.get('appointmentId') or 
                    data.get('id') or 
                    data.get('appointment', {}).get('id') if isinstance(data.get('appointment'), dict) else None
                )
                # Sometimes the ID might be in the event structure
                if not ghl_appointment_id and 'event' in data:
                    event = data.get('event', {})
                    if isinstance(event, dict):
                        ghl_appointment_id = event.get('id')
            
            if ghl_appointment_id:
                print(f"✅ Created appointment in GHL: {ghl_appointment_id}")
                return ghl_appointment_id
            else:
                print(f"⚠️ GHL API response missing appointment ID. Response: {response.text}")
                return None
        else:
            print(f"❌ Failed to create appointment in GHL: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Error creating appointment in GHL: {str(e)}")
        return None


def update_appointment_in_ghl(appointment: Appointment, changed_fields: Optional[Dict[str, Any]] = None) -> bool:
    """
    Update appointment in GHL
    
    Args:
        appointment: Appointment instance to update in GHL
        changed_fields: Dictionary of changed fields (if None, sends all fields)
        
    Returns:
        True if successful, False otherwise
    """
    credentials = get_ghl_credentials()
    if not credentials:
        print("❌ No GHLAuthCredentials found. Cannot sync appointment to GHL.")
        return False
    
    # All appointments should have a GHL appointment ID (they come from GHL webhooks)
    if not appointment.ghl_appointment_id:
        print(f"❌ Appointment {appointment.id} missing ghl_appointment_id. Cannot update in GHL.")
        return False
    
    # Skip if this is a local appointment (shouldn't happen in normal flow, but handle gracefully)
    if appointment.ghl_appointment_id.startswith('local_'):
        print(f"⚠️ Appointment {appointment.id} has local ID. Cannot update in GHL without real GHL appointment ID.")
        return False
    
    headers = get_ghl_headers(credentials.access_token)
    url = f'https://services.leadconnectorhq.com/calendars/events/appointments/{appointment.ghl_appointment_id}'
    
    # Build payload - only include changed fields if provided
    if changed_fields:
        payload = {}
        
        # Map our field names to GHL field names
        field_mapping = {
            'title': 'title',
            'appointment_status': 'appointmentStatus',
            'start_time': 'startTime',
            'end_time': 'endTime',
            'address': 'address',
            'notes': 'description',
            # calendar_id is now a ForeignKey, handled separately
            'ghl_contact_id': 'contactId',
            'assigned_user': 'assignedUserId',
            'ghl_assigned_user_id': 'assignedUserId',
        }
        
        for field, value in changed_fields.items():
            ghl_field = field_mapping.get(field)
            if ghl_field:
                if field == 'appointment_status':
                    payload[ghl_field] = map_appointment_status_to_ghl(value)
                elif field in ['start_time', 'end_time']:
                    payload[ghl_field] = format_datetime_for_ghl(value)
                elif field == 'assigned_user':
                    # Get GHL user ID from User object
                    if value:
                        # value is a User instance from Django ORM
                        if isinstance(value, User):
                            payload[ghl_field] = value.ghl_user_id if value.ghl_user_id else None
                        else:
                            # Fallback: try to get user by ID if value is not a User instance
                            try:
                                user = User.objects.get(id=value)
                                payload[ghl_field] = user.ghl_user_id if user.ghl_user_id else None
                            except (User.DoesNotExist, TypeError, AttributeError):
                                payload[ghl_field] = None
                    else:
                        # Clear assigned user
                        payload[ghl_field] = None
                elif field == 'ghl_assigned_user_id':
                    payload[ghl_field] = value
                else:
                    payload[ghl_field] = value
        
        # Handle calendar field separately (ForeignKey)
        if 'calendar' in changed_fields:
            calendar = changed_fields.get('calendar')
            if calendar:
                # calendar is a Calendar object
                if hasattr(calendar, 'ghl_calendar_id'):
                    payload['calendarId'] = calendar.ghl_calendar_id
                else:
                    # If it's just an ID, try to get the Calendar object
                    try:
                        from accounts.models import Calendar
                        calendar_obj = Calendar.objects.get(ghl_calendar_id=calendar)
                        payload['calendarId'] = calendar_obj.ghl_calendar_id
                    except (Calendar.DoesNotExist, TypeError, AttributeError):
                        payload['calendarId'] = calendar if isinstance(calendar, str) else None
            else:
                payload['calendarId'] = None
        
        # If address is being updated, add location config
        if 'address' in payload and payload['address']:
            payload['meetingLocationType'] = 'custom'
            payload['meetingLocationId'] = 'custom_0'
            payload['overrideLocationConfig'] = True
    else:
        # Send all fields if no changed_fields provided
        payload = {
            'title': appointment.title or 'Appointment',
            'appointmentStatus': map_appointment_status_to_ghl(appointment.appointment_status),
            'startTime': format_datetime_for_ghl(appointment.start_time),
            'endTime': format_datetime_for_ghl(appointment.end_time),
            'ignoreDateRange': False,
            'toNotify': False,
            'ignoreFreeSlotValidation': True,
        }
        
        if appointment.calendar:
            payload['calendarId'] = appointment.calendar.ghl_calendar_id
        
        if appointment.ghl_contact_id:
            payload['contactId'] = appointment.ghl_contact_id
        
        if appointment.address:
            payload['address'] = appointment.address
            payload['meetingLocationType'] = 'custom'
            payload['meetingLocationId'] = 'custom_0'
            payload['overrideLocationConfig'] = True
        
        if appointment.notes:
            payload['description'] = appointment.notes
        
        assigned_user_ghl_id = get_assigned_user_ghl_id(appointment)
        if assigned_user_ghl_id:
            payload['assignedUserId'] = assigned_user_ghl_id
    
    try:
        response = requests.put(url, json=payload, headers=headers)
        
        if response.status_code in [200, 201, 204]:
            print(f"✅ Updated appointment in GHL: {appointment.ghl_appointment_id}")
            return True
        else:
            print(f"❌ Failed to update appointment in GHL: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error updating appointment in GHL: {str(e)}")
        return False


def delete_appointment_from_ghl(appointment: Appointment) -> bool:
    """
    Delete appointment from GHL
    
    Args:
        appointment: Appointment instance to delete from GHL
        
    Returns:
        True if successful, False otherwise
    """
    credentials = get_ghl_credentials()
    if not credentials:
        print("❌ No GHLAuthCredentials found. Cannot sync appointment to GHL.")
        return False
    
    # Skip if this is a local appointment (not synced to GHL)
    if not appointment.ghl_appointment_id or appointment.ghl_appointment_id.startswith('local_'):
        print(f"⚠️ Appointment {appointment.id} is local, not in GHL. Skipping delete.")
        return True
    
    headers = get_ghl_headers(credentials.access_token)
    url = f'https://services.leadconnectorhq.com/calendars/events/{appointment.ghl_appointment_id}'
    
    try:
        response = requests.delete(url, headers=headers, json={})
        
        if response.status_code in [200, 204]:
            print(f"✅ Deleted appointment from GHL: {appointment.ghl_appointment_id}")
            return True
        else:
            print(f"❌ Failed to delete appointment from GHL: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error deleting appointment from GHL: {str(e)}")
        return False


def create_ghl_appointment_from_job(job) -> Optional[Appointment]:
    """
    Create an appointment in GHL from a Job when job status becomes 'confirmed'.
    This function creates the appointment in GHL and saves it to our database.
    
    Args:
        job: Job instance that has status='confirmed'
        
    Returns:
        Appointment instance if successful, None otherwise
    """
    from jobtracker_app.models import Job
    
    print(f"📅 [CREATE APPOINTMENT FROM JOB] Starting for job {job.id}")
    
    # Check if appointment already exists for this job
    if hasattr(job, 'appointment') and job.appointment:
        print(f"⚠️ [CREATE APPOINTMENT FROM JOB] Appointment already exists for job {job.id}: {job.appointment.ghl_appointment_id}")
        return job.appointment
    
    # Resolve location_id
    location_id = None
    try:
        job_with_relations = (
            Job.objects
            .select_related('submission__contact')
            .get(id=job.id)
        )
        
        if job_with_relations.submission and job_with_relations.submission.contact:
            location_id = job_with_relations.submission.contact.location_id
            print(f"📍 [CREATE APPOINTMENT FROM JOB] Location ID: {location_id}")
        else:
            print("⚠️ [CREATE APPOINTMENT FROM JOB] No submission/contact found, using credentials")
            credentials = GHLAuthCredentials.objects.first()
            if credentials:
                location_id = credentials.location_id
                print(f"📍 [CREATE APPOINTMENT FROM JOB] Location ID from credentials: {location_id}")
    except Job.DoesNotExist:
        print("❌ [CREATE APPOINTMENT FROM JOB] Job not found")
        return None
    
    if not location_id:
        print("❌ [CREATE APPOINTMENT FROM JOB] Could not resolve location_id")
        return None
    
    # Get credentials for this location
    try:
        credentials = GHLAuthCredentials.objects.get(location_id=location_id)
    except GHLAuthCredentials.DoesNotExist:
        print(f"❌ [CREATE APPOINTMENT FROM JOB] No GHLAuthCredentials found for location_id: {location_id}")
        return None
    except GHLAuthCredentials.MultipleObjectsReturned:
        print(f"⚠️ [CREATE APPOINTMENT FROM JOB] Multiple credentials found, using first")
        credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
    
    # Get GHL contact ID
    ghl_contact_id = job.ghl_contact_id
    if not ghl_contact_id and job.submission and job.submission.contact:
        ghl_contact_id = job.submission.contact.contact_id
    
    if not ghl_contact_id:
        print("⚠️ [CREATE APPOINTMENT FROM JOB] No GHL contact ID found for job")
        # We can still create the appointment without contact_id, but it's not ideal
    
    # Get all assigned user GHL IDs from job assignments (create same appointment for each assignee)
    assigned_user_ghl_ids = []
    if job.assignments.exists():
        for assignment in job.assignments.select_related('user').all():
            if assignment.user and assignment.user.ghl_user_id:
                assigned_user_ghl_ids.append(assignment.user.ghl_user_id)
        if assigned_user_ghl_ids:
            print(f"📍 [CREATE APPOINTMENT FROM JOB] Creating appointment for {len(assigned_user_ghl_ids)} assignee(s): {assigned_user_ghl_ids}")
        else:
            print("⚠️ [CREATE APPOINTMENT FROM JOB] No assigned users with GHL ID found")
    else:
        print("⚠️ [CREATE APPOINTMENT FROM JOB] No assignments found")
    
    # Get calendar by name and location_id
    calendar = None
    calendar_id = None
    try:
        calendar = Calendar.objects.filter(
            name="Reccuring Service Calendar",
            account__location_id=location_id
        ).first()
        if calendar:
            calendar_id = calendar.ghl_calendar_id
            print(f"📅 [CREATE APPOINTMENT FROM JOB] Found calendar: {calendar.name} (ID: {calendar_id})")
        else:
            print(f"⚠️ [CREATE APPOINTMENT FROM JOB] Calendar 'Reccuring Service Calendar' not found for location_id: {location_id}")
    except Exception as e:
        print(f"❌ [CREATE APPOINTMENT FROM JOB] Error finding calendar: {str(e)}")
    
    # Build appointment payload
    if not job.scheduled_at:
        print("❌ [CREATE APPOINTMENT FROM JOB] Job has no scheduled_at time")
        return None
    
    # Get timezone from credentials and convert job times
    import pytz
    from django.utils import timezone as django_timezone
    
    try:
        timezone_str = credentials.timezone if credentials.timezone else "America/Chicago"
        tz = pytz.timezone(timezone_str)
    except Exception as e:
        print(f"⚠️ [CREATE APPOINTMENT FROM JOB] Error getting timezone, using default: {str(e)}")
        tz = pytz.timezone("America/Chicago")
    
    # Convert job times: treat scheduled_at as local time, then convert to UTC
    try:
        job_start_time = job.scheduled_at
        
        # The job's scheduled_at is stored in UTC but actually represents local time
        # We need to treat it as if it's in the local timezone
        if django_timezone.is_naive(job_start_time):
            # If naive, localize it to the credentials timezone
            job_start_time = tz.localize(job_start_time)
        else:
            # If aware (stored as UTC), we need to treat it as local time
            # So we convert to naive first, then localize to the target timezone
            naive_time = job_start_time.replace(tzinfo=None)
            job_start_time = tz.localize(naive_time)
        
        # Calculate job end time in the same timezone
        from datetime import timedelta
        duration_hours = float(job.duration_hours) if job.duration_hours else 1.0
        job_end_time = job_start_time + timedelta(hours=duration_hours)
        
        # Convert both times to UTC for GHL API (GHL expects UTC)
        start_time_utc = job_start_time.astimezone(pytz.UTC)
        end_time_utc = job_end_time.astimezone(pytz.UTC)
        
        # Format datetime for GHL API
        start_time_str = format_datetime_for_ghl(start_time_utc)
        end_time_str = format_datetime_for_ghl(end_time_utc)
        
        print(f"🕐 [CREATE APPOINTMENT FROM JOB] Time conversion: {job.scheduled_at} (job) -> {start_time_utc} (UTC)")
    except Exception as e:
        print(f"❌ [CREATE APPOINTMENT FROM JOB] Error converting timezone: {str(e)}")
        return None
    
    payload = {
        "title": job.title or "Job Appointment",
        "meetingLocationType": "custom",
        "meetingLocationId": "custom_0",
        "overrideLocationConfig": True,
        "appointmentStatus": "confirmed",
        "description": job.description or job.notes or "",
        "address": job.customer_address or "Zoom",  # Default to "Zoom" if no address
        "ignoreDateRange": False,
        "ignoreFreeSlotValidation": True,
        "locationId": location_id,
        "startTime": start_time_str,
        "endTime": end_time_str,
    }
    
    # Add optional fields
    if calendar_id:
        payload["calendarId"] = calendar_id
    
    if ghl_contact_id:
        payload["contactId"] = ghl_contact_id
    
    # Create one appointment per assignee (same details, different assignedUserId); if no assignees, create one without assignee
    headers = get_ghl_headers(credentials.access_token)
    url = 'https://services.leadconnectorhq.com/calendars/events/appointments'
    assignee_ids_to_use = assigned_user_ghl_ids if assigned_user_ghl_ids else [None]
    created_any = False
    try:
        for assigned_user_ghl_id in assignee_ids_to_use:
            req_payload = {**payload}
            if assigned_user_ghl_id:
                req_payload["assignedUserId"] = assigned_user_ghl_id
            print(f"📤 [CREATE APPOINTMENT FROM JOB] Creating appointment in GHL for job {job.id}" + (f" (assignee: {assigned_user_ghl_id})" if assigned_user_ghl_id else " (no assignee)"))
            response = requests.post(url, json=req_payload, headers=headers)
            
            if response.status_code in [200, 201]:
                data = response.json()
                print(f"✅ [CREATE APPOINTMENT FROM JOB] GHL API response: {data}")
                created_any = True
            else:
                print(f"❌ [CREATE APPOINTMENT FROM JOB] Failed to create appointment in GHL for assignee {assigned_user_ghl_id}: {response.status_code} - {response.text}")
        
        if not created_any:
            return None
            
            # Extract GHL appointment ID from response
            # ghl_appointment_id = None
            # if isinstance(data, dict):
            #     ghl_appointment_id = (
            #         data.get('id') or 
            #         data.get('appointmentId') or 
            #         data.get('appointment', {}).get('id') if isinstance(data.get('appointment'), dict) else None
            #     )
            
            # if not ghl_appointment_id:
            #     print(f"❌ [CREATE APPOINTMENT FROM JOB] GHL API response missing appointment ID. Response: {response.text}")
            #     return None
            
            # print(f"✅ [CREATE APPOINTMENT FROM JOB] Created appointment in GHL: {ghl_appointment_id}")
            
            # # Create appointment in our database
            # # We'll wait for the webhook to create it, but we can also create it here
            # # with a flag to indicate it was created from backend
            # appointment = Appointment.objects.create(
            #     ghl_appointment_id=ghl_appointment_id,
            #     location_id=location_id,
            #     title=payload.get("title"),
            #     address=payload.get("address"),
            #     calendar=calendar,
            #     appointment_status="confirmed",
            #     notes=payload.get("description"),
            #     ghl_contact_id=ghl_contact_id,
            #     ghl_assigned_user_id=assigned_user_ghl_id,
            #     start_time=start_time_utc,
            #     end_time=end_time_utc,
            #     created_from_backend=True,
            #     job=job,
            # )
            
            # # Link contact if available
            # if ghl_contact_id:
            #     from accounts.models import Contact
            #     try:
            #         contact = Contact.objects.get(contact_id=ghl_contact_id)
            #         appointment.contact = contact
            #         appointment.save(update_fields=['contact'])
            #     except Contact.DoesNotExist:
            #         print(f"⚠️ [CREATE APPOINTMENT FROM JOB] Contact {ghl_contact_id} not found")
            
            # # Link assigned user if available
            # if assigned_user_ghl_id:
            #     try:
            #         assigned_user = User.objects.get(ghl_user_id=assigned_user_ghl_id)
            #         appointment.assigned_user = assigned_user
            #         appointment.save(update_fields=['assigned_user'])
            #     except User.DoesNotExist:
            #         print(f"⚠️ [CREATE APPOINTMENT FROM JOB] User {assigned_user_ghl_id} not found")
            
            # print(f"✅ [CREATE APPOINTMENT FROM JOB] Created appointment {appointment.id} for job {job.id}")
            # return appointment
            
    except Exception as e:
        print(f"❌ [CREATE APPOINTMENT FROM JOB] Error creating appointment in GHL: {str(e)}")
        return None
