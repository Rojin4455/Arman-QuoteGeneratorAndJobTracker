"""
GHL Appointment Sync Utilities
Handles syncing appointments with GoHighLevel API
"""
import requests
from typing import Dict, Any, Optional
from django.utils import timezone
from accounts.models import GHLAuthCredentials
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
    if appointment.calendar_id:
        payload['calendarId'] = appointment.calendar_id
    
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
    
    # Skip if this is a local appointment (not synced to GHL yet)
    if not appointment.ghl_appointment_id or appointment.ghl_appointment_id.startswith('local_'):
        print(f"⚠️ Appointment {appointment.id} is local, not synced to GHL. Creating instead...")
        ghl_id = create_appointment_in_ghl(appointment)
        if ghl_id:
            appointment.ghl_appointment_id = ghl_id
            appointment.save(update_fields=['ghl_appointment_id'])
        return ghl_id is not None
    
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
            'calendar_id': 'calendarId',
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
        
        if appointment.calendar_id:
            payload['calendarId'] = appointment.calendar_id
        
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
