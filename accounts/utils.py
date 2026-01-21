import requests
import time
import re
from typing import List, Dict, Any, Optional
from django.utils.dateparse import parse_datetime
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from accounts.models import GHLAuthCredentials, Contact, Address, Calendar, GHLLocationIndex, GHLCustomField
from service_app.models import User, Appointment


def fetch_all_contacts(location_id: str, access_token: str = None) -> List[Dict[str, Any]]:
    """
    Fetch all contacts from GoHighLevel API with proper pagination handling.
    
    Args:
        location_id (str): The location ID for the subaccount
        access_token (str, optional): Bearer token for authentication
        
    Returns:
        List[Dict]: List of all contacts
    """

    
    
    
    
    base_url = "https://services.leadconnectorhq.com/contacts/"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }
    
    all_contacts = []
    start_after = None
    start_after_id = None
    page_count = 0
    
    while True:
        page_count += 1
        print(f"Fetching page {page_count}...")
        
        # Set up parameters for current request
        params = {
            "locationId": location_id,
            "limit": 100,  # Maximum allowed by API
        }
        
        # Add pagination parameters if available
        if start_after:
            params["startAfter"] = start_after
        if start_after_id:
            params["startAfterId"] = start_after_id
            
        try:
            response = requests.get(base_url, headers=headers, params=params)
            
            if response.status_code != 200:
                print(f"Error Response: {response.status_code}")
                print(f"Error Details: {response.text}")
                raise Exception(f"API Error: {response.status_code}, {response.text}")
            
            data = response.json()
            
            # Get contacts from response
            contacts = data.get("contacts", [])
            if not contacts:
                print("No more contacts found.")
                break
                
            all_contacts.extend(contacts)
            print(f"Retrieved {len(contacts)} contacts. Total so far: {len(all_contacts)}")
            
            # Check if there are more pages
            # GoHighLevel API uses cursor-based pagination
            meta = data.get("meta", {})
            
            # Update pagination cursors for next request
            if contacts:  # If we got contacts, prepare for next page
                last_contact = contacts[-1]
                
                # Get the ID for startAfterId (this should be a string)
                if "id" in last_contact:
                    start_after_id = last_contact["id"]
                
                # Get timestamp for startAfter (this must be a number/timestamp)
                start_after = None
                if "dateAdded" in last_contact:
                    # Convert to timestamp if it's a string
                    date_added = last_contact["dateAdded"]
                    if isinstance(date_added, str):
                        try:
                            from datetime import datetime
                            # Try parsing ISO format
                            dt = datetime.fromisoformat(date_added.replace('Z', '+00:00'))
                            start_after = int(dt.timestamp() * 1000)  # Convert to milliseconds
                        except:
                            # Try parsing as timestamp
                            try:
                                start_after = int(float(date_added))
                            except:
                                pass
                    elif isinstance(date_added, (int, float)):
                        start_after = int(date_added)
                        
                elif "createdAt" in last_contact:
                    created_at = last_contact["createdAt"]
                    if isinstance(created_at, str):
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            start_after = int(dt.timestamp() * 1000)
                        except:
                            try:
                                start_after = int(float(created_at))
                            except:
                                pass
                    elif isinstance(created_at, (int, float)):
                        start_after = int(created_at)
            
            # Check if we've reached the end
            total_count = meta.get("total", 0)
            if total_count > 0 and len(all_contacts) >= total_count:
                print(f"Retrieved all {total_count} contacts.")
                break
                
            # If we got fewer contacts than the limit, we're likely at the end
            if len(contacts) < 100:
                print("Retrieved fewer contacts than limit, likely at end.")
                break
                
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            raise
        except Exception as e:
            print(f"Unexpected error: {e}")
            raise
            
        # Add a small delay to be respectful to the API
        time.sleep(0.1)
        
        # Safety check to prevent infinite loops
        if page_count > 1000:  # Adjust based on expected contact count
            print("Warning: Stopped after 1000 pages to prevent infinite loop")
            break
    
    print(f"\nTotal contacts retrieved: {len(all_contacts)}")

    # sync_contacts_to_db(all_contacts)
    fetch_contacts_locations(all_contacts, location_id, access_token)
    # return all_contacts




def sync_contacts_to_db(contact_data):
    """
    Syncs contact data from API into the local Contact model using bulk upsert.
    Also deletes any Contact objects not present in the incoming contact_data.
    Args:
        contact_data (list): List of contact dicts from GoHighLevel API
    """
    contacts_to_create = []
    incoming_ids = set(c['id'] for c in contact_data)
    existing_ids = set(Contact.objects.filter(contact_id__in=incoming_ids).values_list('contact_id', flat=True))

    for item in contact_data:
        date_added = parse_datetime(item.get("dateAdded")) if item.get("dateAdded") else None
        contact_obj = Contact(
            contact_id=item.get("id"),
            first_name=item.get("firstName"),
            last_name=item.get("lastName"),
            phone=item.get("phone"),
            email=item.get("email"),
            dnd=item.get("dnd", False),
            country=item.get("country"),
            date_added=date_added,
            tags=item.get("tags", []),
            custom_fields=item.get("customFields", []),
            location_id=item.get("locationId"),
            timestamp=date_added
        )
        if item.get("id") in existing_ids:
            # Update existing contact
            Contact.objects.filter(contact_id=item["id"]).update(
                first_name=contact_obj.first_name,
                last_name=contact_obj.last_name,
                phone=contact_obj.phone,
                email=contact_obj.email,
                dnd=contact_obj.dnd,
                country=contact_obj.country,
                date_added=contact_obj.date_added,
                tags=contact_obj.tags,
                custom_fields=contact_obj.custom_fields,
                location_id=contact_obj.location_id,
                timestamp=contact_obj.timestamp
            )
        else:
            contacts_to_create.append(contact_obj)

    if contacts_to_create:
        with transaction.atomic():
            Contact.objects.bulk_create(contacts_to_create, ignore_conflicts=True)

    # Delete contacts not present in the incoming data
    deleted_count, _ = Contact.objects.exclude(contact_id__in=incoming_ids).delete()

    print(f"{len(contacts_to_create)} new contacts created.")
    print(f"{len(existing_ids)} existing contacts updated.")
    print(f"{deleted_count} contacts deleted as they were not present in the latest data.")





def create_ghl_location_index(location_id: str):
    """
    Create GHLLocationIndex entries for a given location_id
    """

    location_index = {
        "address_0": 0,
        "QmYk134LkK2hownvL1sE": 1,
        "6K2aY5ghsAeCNhNJBCt": 2,
        "4vx8hTmhneL3aHhQOobV": 3,
        "ou8hGYQTDuijrxtCD2Bhs": 4,
        "IVh5iKD6A7xB6J0CqocG": 5,
        "vsrkHtczxuyyIg9CG80p": 6,
        "tt28EWemd1DyWpzqQKA3": 7,
        "1ERLsUjWpMrUfHZx1oIr": 8,
        "cCpLI0tAY2q2MfCM5yco": 9,
        "cDlPlyq0J77lx2G1U88G": 10,
    }

    # Fetch credentials
    credentials = GHLAuthCredentials.objects.filter(
        location_id=location_id
    ).first()

    if not credentials:
        raise ValueError(f"No GHL credentials found for location_id: {location_id}")

    location_objects = []

    for parent_id, order in location_index.items():
        location_objects.append(
            GHLLocationIndex(
                account=credentials,
                parent_id=parent_id,
                order=order,
                name=f"Address {order}"
            )
        )

    GHLLocationIndex.objects.bulk_create(location_objects)

    return len(location_objects)


def fetch_contacts_locations(contact_data: list, location_id: str, access_token: str) -> dict:
    # Fetch location custom fields
    location_custom_fields = fetch_location_custom_fields(location_id, access_token)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }
    
    # Get Property Sqft custom field ID dynamically (once before the loop)
    property_sqft_field_id = None
    try:
        credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if credentials:
            property_sqft_field = GHLCustomField.objects.filter(
                account=credentials,
                field_name='Property Sqft',
                is_active=True
            ).first()
            if property_sqft_field:
                property_sqft_field.refresh_from_db()
                if property_sqft_field.ghl_field_id and property_sqft_field.ghl_field_id != 'ghl_field_id' and len(property_sqft_field.ghl_field_id) >= 5:
                    property_sqft_field_id = property_sqft_field.ghl_field_id
                    print(f"✅ [PROPERTY SQFT] Using custom field 'Property Sqft' with ID: {property_sqft_field_id}")
    except Exception as e:
        print(f"⚠️ [PROPERTY SQFT] Error fetching Property Sqft field: {str(e)}")
    
    total_contacts = len(contact_data)

    for idx, contact in enumerate(contact_data, 1):
        print(f"Processing contact {idx}/{total_contacts}")  # Progress for each contact
        contact_id = contact.get("id")
        if not contact_id:
            continue
        url = f"https://services.leadconnectorhq.com/contacts/{contact_id}"
        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print(f"Error fetching contact details for {contact_id}: {response.status_code}")
                print(f"Error details: {response.text}")
                continue
            data = response.json()
            contact_detail = data.get('contact', {})
            # --- Address 0 extraction ---

            address_fields = {
                'street_address': contact_detail.get('address1'),
                'city': contact_detail.get('city'),
                'state': contact_detail.get('state'),
                'postal_code': contact_detail.get('postalCode'),
                # 'country': contact_detail.get('country'),  # Uncomment if Address model has country
                'address_id': 'address_0',
                'order': 0,
                'name': 'Address 0',
                'contact_id': contact_id
            }

            # Extract property_sqft from custom fields if field ID is found
            if property_sqft_field_id:
                for field in contact_detail.get("customFields", []):
                    if field.get("id") == property_sqft_field_id:
                        address_fields["property_sqft"] = field.get("value")
                        break

            
            # Only save if at least one address field is present
            if any(address_fields.get(f) for f in ['street_address', 'city', 'state', 'postal_code']):
                sync_addresses_to_db([address_fields])
            # --- Custom fields addresses ---
            custom_fields = contact_detail.get('customFields', [])
            if custom_fields and any(cf.get('value') for cf in custom_fields):
                create_address_from_custom_fields(contact_id, custom_fields, location_custom_fields, location_id)
                # Add a small delay to be respectful to the API
            time.sleep(0.2)

        except requests.exceptions.RequestException as e:
            print(f"Request failed for {contact_id}: {e}")
            continue


def fetch_location_custom_fields(location_id: str, access_token: str) -> dict:
    """
    Fetch custom fields for a given location from GoHighLevel API and return a dict with id as key and a dict of name, fieldKey, parentId as value.

    Args:
        location_id (str): The location ID for the subaccount
        access_token (str): Bearer token for authentication

    Returns:
        dict: {id: {"name": ..., "fieldKey": ..., "parentId": ...}, ...}
    Raises:
        Exception: If the API request fails
    """
    url = f"https://services.leadconnectorhq.com/locations/{location_id}/customFields?model=contact"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        fields = data.get("customFields", [])
        return {
            f.get("id"): {
                "name": f.get("name"),
                "fieldKey": f.get("fieldKey"),
                "parentId": f.get("parentId")
            }
            for f in fields if f.get("id")
        }
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        raise Exception(f"Failed to fetch custom fields: {e}")


def create_address_from_custom_fields(contact_id: str, custom_fields_list: list, location_custom_fields: dict, location_id: str = None):
    """
    Create Address instances in the DB from a contact's custom fields dict, using the location_custom_fields mapping.
    Args:
        contact_id (str): The contact's unique ID (should exist in Contact model)
        custom_fields_list (list): List of dicts with 'id' and 'value' for each custom field
        location_custom_fields (dict): Mapping of field IDs to their metadata
        location_id (str, optional): The location ID for the subaccount (used to fetch location_index from model)
    Returns:
        None (prints sync summary)
    """
    
    # Get location_index from model using location_id
    location_index = {}
    if location_id:
        try:
            # Get credentials for this location
            credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
            if credentials:
                # Fetch all active location indices for this account, ordered by order
                location_indices = GHLLocationIndex.objects.filter(
                    account=credentials,
                    is_active=True
                ).order_by('order')
                
                # Build location_index dictionary from model
                for loc_idx in location_indices:
                    location_index[loc_idx.parent_id] = loc_idx.order
                
                if location_index:
                    print(f"✅ [LOCATION INDEX] Loaded {len(location_index)} location indices from model for location_id: {location_id}")
                else:
                    print(f"⚠️ [LOCATION INDEX] No location indices found in model for location_id: {location_id}, using empty dict")
            else:
                print(f"⚠️ [LOCATION INDEX] No credentials found for location_id: {location_id}, using empty dict")
        except Exception as e:
            print(f"❌ [LOCATION INDEX] Error fetching location_index from model: {str(e)}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️ [LOCATION INDEX] No location_id provided, using empty dict")

    # Group custom fields by parentId (location)
    address_fields = {pid: {} for pid in location_index}
    for field in custom_fields_list:
        field_id = field.get('id')
        value = field.get('value')
        meta = location_custom_fields.get(field_id, {})
        parent_id = meta.get('parentId')
        field_key = meta.get('fieldKey') or meta.get('name')
        if parent_id and parent_id in location_index and field_key:
            # Remove 'contact.' prefix and strip numeric suffix (e.g., _0, _1, _2, etc.)
            clean_key = field_key.replace('contact.', '')
            base_key = re.sub(r'_[0-9]+$', '', clean_key)
            address_fields[parent_id][base_key] = value  # last value wins if duplicate

    # Prepare address dicts for sync_addresses_to_db
    all_address_model_fields = ['state', 'street_address', 'city', 'postal_code', 'gate_code', 'number_of_floors', 'property_sqft', 'property_type']
    address_dicts = []
    for parent_id, field_map in address_fields.items():
        if not field_map:
            continue
        address_data = {field: field_map.get(field) for field in all_address_model_fields}
        # Convert types if needed
        if address_data['number_of_floors'] is not None:
            try:
                address_data['number_of_floors'] = int(address_data['number_of_floors'])
            except Exception:
                address_data['number_of_floors'] = None
        if address_data['property_sqft'] is not None:
            try:
                address_data['property_sqft'] = int(address_data['property_sqft'])
            except Exception:
                address_data['property_sqft'] = None
        address_data['address_id'] = parent_id
        address_data['order'] = location_index[parent_id]
        address_data['name'] = f"Address {location_index[parent_id]}"
        address_data['contact_id'] = contact_id
        address_dicts.append(address_data)
    # Call sync_addresses_to_db
    sync_addresses_to_db(address_dicts)




def sync_addresses_to_db(address_data):
    """
    Syncs address data from API into the local Address model using bulk upsert.
    Args:
        address_data (list): List of address dicts, each must include contact_id and address_id
    """

    addresses_to_create = []
    updated_count = 0
    # Build a set of (contact_id, address_id) for existing addresses
    existing = set(
        Address.objects.filter(
            contact__contact_id__in=[a['contact_id'] for a in address_data],
            address_id__in=[a['address_id'] for a in address_data]
        ).values_list('contact__contact_id', 'address_id')
    )

    for item in address_data:
        contact_id = item.get('contact_id')
        address_id = item.get('address_id')
        if not contact_id or not address_id:
            continue
        try:
            contact = Contact.objects.get(contact_id=contact_id)
        except ObjectDoesNotExist:
            print(f"Contact with id {contact_id} does not exist. Skipping address.")
            continue
        address_fields = item.copy()
        address_fields.pop('contact_id', None)
        address_fields.pop('address_id', None)
        if (contact_id, address_id) in existing:
            # Update existing address
            Address.objects.filter(contact=contact, address_id=address_id).update(**address_fields)
            updated_count += 1
        else:
            addresses_to_create.append(Address(contact=contact, address_id=address_id, **address_fields))
    if addresses_to_create:
        with transaction.atomic():
            Address.objects.bulk_create(addresses_to_create, ignore_conflicts=True)
    print(f"{len(addresses_to_create)} new addresses created.")
    print(f"{updated_count} existing addresses updated.")





def create_or_update_contact(data):
    # Handle nested webhook payload structure (similar to appointments)
    if "contact" in data:
        contact_data = data["contact"]
        # Get location_id from root if not in nested contact
        if "locationId" not in contact_data:
            contact_data["locationId"] = data.get("locationId")
    else:
        contact_data = data
    
    contact_id = contact_data.get("id")
    if not contact_id:
        print("❌ [WEBHOOK] Contact ID is required in webhook payload")
        return None
    
    # Ensure location_id is present (required field, NOT NULL)
    location_id = contact_data.get("locationId")
    if not location_id:
        # Try to get from root data
        location_id = data.get("locationId")
        if not location_id:
            # Fallback to credentials
            cred = GHLAuthCredentials.objects.first()
            if cred and cred.location_id:
                location_id = cred.location_id
            else:
                print(f"❌ [WEBHOOK] locationId is required for contact {contact_id}")
                return None
    
    try:
        # Parse date_added if provided
        date_added = None
        if contact_data.get("dateAdded"):
            date_added = parse_datetime(contact_data.get("dateAdded"))
        
        # Handle dnd field - GHL may send None, but database requires boolean
        dnd_value = contact_data.get("dnd")
        if dnd_value is None:
            dnd_value = False
        
        contact, created = Contact.objects.update_or_create(
            contact_id=contact_id,
            defaults={
                "first_name": contact_data.get("firstName"),
                "last_name": contact_data.get("lastName"),
                "email": contact_data.get("email"),
                "phone": contact_data.get("phone"),
                "dnd": dnd_value,  # Ensure this is always a boolean, never None
                "country": contact_data.get("country"),
                "date_added": date_added,
                "location_id": location_id,  # Ensure this is never None
                "custom_fields": contact_data.get("customFields", []),
                "tags": contact_data.get("tags", []),
            }
        )
        
        cred = GHLAuthCredentials.objects.first()
        if cred:
            fetch_contacts_locations([contact_data], location_id, cred.access_token)
        
        print(f"✅ Contact {'created' if created else 'updated'}: {contact_id}")
        return contact
    except Exception as e:
        print(f"❌ Error creating/updating contact {contact_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def delete_contact(data):
    contact_id = data.get("id")
    try:
        contact = Contact.objects.get(contact_id=contact_id)
        # Delete all addresses related to this contact
        Address.objects.filter(contact=contact).delete()
        contact.delete()
        print("Contact and related addresses deleted:", contact_id)
    except Contact.DoesNotExist:
        print("Contact not found for deletion:", contact_id)


def create_or_update_user_from_ghl(user_data: Dict[str, Any]) -> User:
    """
    Create or update a User from GHL user data.
    If user exists (by ghl_user_id or email), update it. Otherwise, create new.
    Sets user as worker initially and saves GHL user ID.
    
    Args:
        user_data (dict): User data from GHL API or webhook payload with fields:
            - id: GHL user ID
            - name: Full name
            - firstName: First name
            - lastName: Last name
            - email: Email address
            - phone: Phone number
            Or nested structure with 'user' key containing the user data
    
    Returns:
        User: The created or updated User instance
    """
    # Handle nested webhook payload structure (if user data is nested)
    if "user" in user_data:
        user_data = user_data["user"]
    
    ghl_user_id = user_data.get("id")
    email = user_data.get("email")
    first_name = user_data.get("firstName", "")
    last_name = user_data.get("lastName", "")
    phone = user_data.get("phone", "")
    full_name = user_data.get("name", "")
    
    # Generate username from email or use a default
    # Use email as username, or generate from GHL user ID
    base_username = email if email else f"user_{ghl_user_id}"
    username = base_username
    
    # Try to find existing user by ghl_user_id first, then by email
    user = None
    if ghl_user_id:
        try:
            user = User.objects.get(ghl_user_id=ghl_user_id)
        except User.DoesNotExist:
            pass
    
    if not user and email:
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            pass
    
    # If creating new user, ensure username is unique
    if not user:
        counter = 1
        while User.objects.filter(username=username).exists():
            if email:
                # If email exists, append counter
                local_part, domain = email.split('@', 1)
                username = f"{local_part}_{counter}@{domain}"
            else:
                username = f"user_{ghl_user_id}_{counter}"
            counter += 1
    
    # Prepare update/create defaults
    defaults = {
        "ghl_user_id": ghl_user_id,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "role": User.ROLE_WORKER,  # Set as worker initially
    }
    
    if user:
        # Update existing user
        # If no username is set, use the generated one
        if not user.username:
            defaults["username"] = username
        for key, value in defaults.items():
            setattr(user, key, value)
        user.save()
        print(f"User updated: {email or ghl_user_id}")
    else:
        # Create new user - username is passed separately, not in defaults
        user = User.objects.create(
            username=username,
            **defaults
        )
        # Set password as email address
        if email:
            user.set_password(email)
        else:
            # If no email, set password as GHL user ID
            user.set_password(ghl_user_id or username)
        user.save()
        print(f"User created: {email or ghl_user_id}")
    
    return user


def fetch_all_users_from_ghl(location_id: str, access_token: str) -> List[Dict[str, Any]]:
    """
    Fetch all users from GoHighLevel API for a given location.
    
    Args:
        location_id (str): The location ID for the subaccount
        access_token (str): Bearer token for authentication
        
    Returns:
        List[Dict]: List of all users from GHL API
    """
    url = "https://services.leadconnectorhq.com/users/"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }
    
    params = {
        "locationId": location_id
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        users = data.get("users", [])
        print(f"Fetched {len(users)} users from GHL")
        return users
    except requests.exceptions.RequestException as e:
        print(f"Error fetching users from GHL: {e}")
        raise Exception(f"Failed to fetch users: {e}")


def sync_all_users_to_db(location_id: str, access_token: str) -> Dict[str, int]:
    """
    Fetch all users from GHL API and sync them to the local User database.
    
    Args:
        location_id (str): The location ID for the subaccount
        access_token (str): Bearer token for authentication
        
    Returns:
        Dict: Summary with counts of created and updated users
    """
    users_data = fetch_all_users_from_ghl(location_id, access_token)
    
    created_count = 0
    updated_count = 0
    
    for user_data in users_data:
        # Check if user exists before creating/updating
        ghl_user_id = user_data.get("id")
        email = user_data.get("email")
        
        user_exists = False
        if ghl_user_id:
            user_exists = User.objects.filter(ghl_user_id=ghl_user_id).exists()
        if not user_exists and email:
            user_exists = User.objects.filter(email=email).exists()
        
        user = create_or_update_user_from_ghl(user_data)
        
        if user_exists:
            updated_count += 1
        else:
            created_count += 1
    
    print(f"User sync completed: {created_count} created, {updated_count} updated")
    return {
        "created": created_count,
        "updated": updated_count,
        "total": len(users_data)
    }


def update_all_users_password(password: str = "adminuser@246!") -> Dict[str, int]:
    """
    Update password for all existing users in the database.
    
    Args:
        password (str): The password to set for all users. Defaults to "adminuser@246!"
        
    Returns:
        Dict: Summary with count of updated users
    """
    users = User.objects.all()
    updated_count = 0
    
    for user in users:
        user.set_password(password)
        user.save(update_fields=['password'])
        updated_count += 1
        print(f"Password updated for user: {user.username} ({user.email or 'no email'})")
    
    print(f"Password update completed: {updated_count} users updated")
    return {
        "updated": updated_count,
        "total": users.count()
    }


def create_or_update_appointment_from_ghl(appointment_data: Dict[str, Any], location_id: str = None) -> Appointment:
    """
    Create or update an Appointment from GHL appointment data.
    Maps assignedUserId and users array to User model using ghl_user_id.
    
    If appointment was created from our backend (has created_from_backend=True),
    we update it instead of creating a new one. This prevents duplicates when
    the webhook comes back after we create an appointment in GHL.
    
    Args:
        appointment_data (dict): Appointment data from GHL webhook with fields:
            - id: GHL appointment ID
            - title: Appointment title
            - address: Meeting URL or address
            - calendarId: Calendar ID
            - contactId: GHL contact ID
            - groupId: Group ID
            - appointmentStatus: Status of appointment
            - assignedUserId: GHL user ID of assigned user
            - users: Array of GHL user IDs
            - notes: Appointment notes
            - source: Source of appointment
            - startTime: Start time (ISO format)
            - endTime: End time (ISO format)
            - dateAdded: Date added (ISO format)
            - dateUpdated: Date updated (ISO format)
        location_id (str): Location ID from webhook payload (optional, can be in appointment_data)
    
    Returns:
        Appointment: The created or updated Appointment instance
    """
    # Handle nested webhook payload structure
    if "appointment" in appointment_data:
        appointment_data = appointment_data["appointment"]
        if not location_id:
            location_id = appointment_data.get("locationId")
    
    ghl_appointment_id = appointment_data.get("id")
    if not ghl_appointment_id:
        raise ValueError("Appointment ID is required")
    
    # Check if appointment already exists (created from backend)
    existing_appointment = Appointment.objects.filter(ghl_appointment_id=ghl_appointment_id).first()
    
    # Parse datetime fields
    start_time = parse_datetime(appointment_data.get("startTime")) if appointment_data.get("startTime") else None
    end_time = parse_datetime(appointment_data.get("endTime")) if appointment_data.get("endTime") else None
    date_added = parse_datetime(appointment_data.get("dateAdded")) if appointment_data.get("dateAdded") else None
    date_updated = parse_datetime(appointment_data.get("dateUpdated")) if appointment_data.get("dateUpdated") else None
    
    # If appointment exists and was created from backend, update it
    if existing_appointment and existing_appointment.created_from_backend:
        print(f"🔄 [WEBHOOK] Updating existing appointment created from backend: {ghl_appointment_id}")
        # Get Calendar object if calendarId is provided
        calendar_id_str = appointment_data.get("calendarId")
        calendar = None
        if calendar_id_str:
            try:
                calendar = Calendar.objects.filter(ghl_calendar_id=calendar_id_str).first()
            except Exception as e:
                print(f"Warning: Could not find calendar with ID {calendar_id_str}: {str(e)}")
        
        # Update the existing appointment with webhook data
        existing_appointment.location_id = location_id or appointment_data.get("locationId", existing_appointment.location_id or "")
        existing_appointment.title = appointment_data.get("title", existing_appointment.title)
        existing_appointment.address = appointment_data.get("address", existing_appointment.address)
        if calendar:
            existing_appointment.calendar = calendar
        existing_appointment.appointment_status = appointment_data.get("appointmentStatus", existing_appointment.appointment_status)
        existing_appointment.source = appointment_data.get("source", existing_appointment.source)
        existing_appointment.notes = appointment_data.get("notes", existing_appointment.notes)
        existing_appointment.ghl_contact_id = appointment_data.get("contactId", existing_appointment.ghl_contact_id)
        existing_appointment.group_id = appointment_data.get("groupId", existing_appointment.group_id)
        existing_appointment.ghl_assigned_user_id = appointment_data.get("assignedUserId", existing_appointment.ghl_assigned_user_id)
        existing_appointment.start_time = start_time or existing_appointment.start_time
        existing_appointment.end_time = end_time or existing_appointment.end_time
        existing_appointment.date_added = date_added or existing_appointment.date_added
        existing_appointment.date_updated = date_updated or existing_appointment.date_updated
        existing_appointment.users_ghl_ids = appointment_data.get("users", existing_appointment.users_ghl_ids)
        # Keep created_from_backend flag as True
        existing_appointment.save()
        appointment = existing_appointment
        created = False
    else:
        # Get Calendar object if calendarId is provided
        calendar_id_str = appointment_data.get("calendarId")
        calendar = None
        if calendar_id_str:
            try:
                calendar = Calendar.objects.filter(ghl_calendar_id=calendar_id_str).first()
                if not calendar:
                    print(f"⚠️ [WEBHOOK] Calendar with GHL ID '{calendar_id_str}' not found in database. Appointment will be created without calendar.")
            except Exception as e:
                print(f"⚠️ [WEBHOOK] Error finding calendar with ID {calendar_id_str}: {str(e)}")
                calendar = None
        
        # Get or create appointment (normal flow for appointments created in GHL)
        # Only include calendar in defaults if it's not None to avoid any issues
        defaults_dict = {
            "location_id": location_id or appointment_data.get("locationId", ""),
            "title": appointment_data.get("title"),
            "address": appointment_data.get("address"),
            "appointment_status": appointment_data.get("appointmentStatus"),
            "source": appointment_data.get("source"),
            "notes": appointment_data.get("notes"),
            "ghl_contact_id": appointment_data.get("contactId"),
            "group_id": appointment_data.get("groupId"),
            "ghl_assigned_user_id": appointment_data.get("assignedUserId"),
            "start_time": start_time,
            "end_time": end_time,
            "date_added": date_added,
            "date_updated": date_updated,
            "users_ghl_ids": appointment_data.get("users", []),
            "created_from_backend": False,  # This is from GHL webhook
        }
        
        # Only add calendar if it exists (ForeignKey can be None)
        if calendar is not None:
            defaults_dict["calendar"] = calendar
        
        appointment, created = Appointment.objects.update_or_create(
            ghl_appointment_id=ghl_appointment_id,
            defaults=defaults_dict
        )
    
    # Set flag to prevent sync back to GHL (this is from GHL webhook)
    appointment._skip_ghl_sync = True
    
    # Link contact if ghl_contact_id exists
    if appointment.ghl_contact_id:
        try:
            contact = Contact.objects.get(contact_id=appointment.ghl_contact_id)
            appointment.contact = contact
            # Keep flag set to prevent sync
            appointment._skip_ghl_sync = True
            appointment.save(update_fields=['contact'])
        except Contact.DoesNotExist:
            print(f"Contact with ID {appointment.ghl_contact_id} not found")
    
    # Link assigned user if ghl_assigned_user_id exists
    if appointment.ghl_assigned_user_id:
        try:
            assigned_user = User.objects.get(ghl_user_id=appointment.ghl_assigned_user_id)
            appointment.assigned_user = assigned_user
            # Keep flag set to prevent sync
            appointment._skip_ghl_sync = True
            appointment.save(update_fields=['assigned_user'])
        except User.DoesNotExist:
            print(f"User with GHL ID {appointment.ghl_assigned_user_id} not found")
    
    # Link users from users array
    users_ghl_ids = appointment_data.get("users", [])
    if users_ghl_ids:
        users_to_add = []
        for ghl_user_id in users_ghl_ids:
            try:
                user = User.objects.get(ghl_user_id=ghl_user_id)
                users_to_add.append(user)
            except User.DoesNotExist:
                print(f"User with GHL ID {ghl_user_id} not found")
        
        # Clear existing users and add new ones
        appointment.users.clear()
        if users_to_add:
            appointment.users.add(*users_to_add)
    
    print(f"Appointment {'created' if created else 'updated'}: {ghl_appointment_id}")
    return appointment


def delete_appointment_from_ghl_webhook(appointment_data: Dict[str, Any]) -> bool:
    """
    Delete an Appointment from our database when GHL sends a delete webhook.
    
    Args:
        appointment_data (dict): Appointment data from GHL webhook with fields:
            - id: GHL appointment ID (required)
            - appointment: nested appointment data (optional)
    
    Returns:
        bool: True if appointment was deleted, False otherwise
    """
    from service_app.models import Appointment
    
    # Handle nested webhook payload structure
    if "appointment" in appointment_data:
        appointment_data = appointment_data["appointment"]
    
    ghl_appointment_id = appointment_data.get("id")
    
    if not ghl_appointment_id:
        print("❌ [WEBHOOK DELETE] Appointment ID is required in webhook payload")
        return False
    
    try:
        appointment = Appointment.objects.get(ghl_appointment_id=ghl_appointment_id)
        # Set flag to prevent sync back to GHL (this is from GHL webhook)
        appointment._skip_ghl_sync = True
        appointment.delete()
        print(f"✅ [WEBHOOK DELETE] Deleted appointment: {ghl_appointment_id}")
        return True
    except Appointment.DoesNotExist:
        print(f"⚠️ [WEBHOOK DELETE] Appointment with GHL ID {ghl_appointment_id} not found in database")
        return False
    except Exception as e:
        print(f"❌ [WEBHOOK DELETE] Error deleting appointment {ghl_appointment_id}: {str(e)}")
        return False


def sync_calendars_from_ghl(location_id: str = None, access_token: str = None) -> List[Dict[str, Any]]:
    """
    Fetch calendars from GoHighLevel API and sync them to the database.
    
    Args:
        location_id (str, optional): The location ID for the subaccount. If not provided, 
                                     will use the first GHLAuthCredentials location_id.
        access_token (str, optional): Bearer token for authentication. If not provided,
                                     will fetch from GHLAuthCredentials.
        
    Returns:
        List[Dict]: List of synced calendar data with status information
    """
    try:
        # Get credentials if not provided
        credentials = None
        if location_id:
            credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        else:
            credentials = GHLAuthCredentials.objects.first()
        
        if not credentials:
            print("❌ [CALENDAR SYNC] No GHLAuthCredentials found in DB.")
            return []
        
        access_token = access_token or credentials.access_token
        location_id = location_id or credentials.location_id
        
        if not access_token or not location_id:
            print("❌ [CALENDAR SYNC] Missing access_token or location_id.")
            return []
        
        # Make API call to fetch calendars
        base_url = "https://services.leadconnectorhq.com/calendars/"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Version": "2021-04-15"
        }
        
        params = {
            "locationId": location_id
        }
        
        print(f"🔹 [CALENDAR SYNC] Fetching calendars for location_id: {location_id}")
        response = requests.get(base_url, headers=headers, params=params)
        
        if response.status_code != 200:
            print(f"❌ [CALENDAR SYNC] Error Response: {response.status_code}")
            print(f"❌ [CALENDAR SYNC] Error Details: {response.text}")
            return []
        
        data = response.json()
        calendars = data.get("calendars", [])
        
        if not calendars:
            print("⚠️ [CALENDAR SYNC] No calendars found in API response.")
            return []
        
        print(f"✅ [CALENDAR SYNC] Found {len(calendars)} calendars in API response.")
        
        synced_calendars = []
        
        # Sync each calendar to database
        for calendar_data in calendars:
            try:
                ghl_calendar_id = calendar_data.get("id")
                if not ghl_calendar_id:
                    print("⚠️ [CALENDAR SYNC] Calendar missing ID, skipping...")
                    continue
                
                # Extract only the fields we need
                calendar_obj, created = Calendar.objects.update_or_create(
                    ghl_calendar_id=ghl_calendar_id,
                    defaults={
                        "account": credentials,
                        "name": calendar_data.get("name", ""),
                        "description": calendar_data.get("description", ""),
                        "widget_type": calendar_data.get("widgetType", ""),
                        "calendar_type": calendar_data.get("calendarType", ""),
                        "widget_slug": calendar_data.get("widgetSlug", ""),
                        "group_id": calendar_data.get("groupId", "") or None,
                    }
                )
                
                action = "Created" if created else "Updated"
                print(f"✅ [CALENDAR SYNC] {action} calendar: {calendar_obj.name} ({ghl_calendar_id})")
                
                synced_calendars.append({
                    "id": ghl_calendar_id,
                    "name": calendar_obj.name,
                    "status": action,
                    "created": created
                })
                
            except Exception as e:
                print(f"❌ [CALENDAR SYNC] Error syncing calendar {calendar_data.get('id', 'unknown')}: {str(e)}")
                continue
        
        print(f"✅ [CALENDAR SYNC] Successfully synced {len(synced_calendars)} calendars.")
        return synced_calendars
        
    except Exception as e:
        print(f"❌ [CALENDAR SYNC] Error in sync_calendars_from_ghl: {str(e)}")
        return []