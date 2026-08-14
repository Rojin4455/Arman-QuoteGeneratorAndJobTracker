from decimal import Decimal
from urllib.parse import urlencode
import uuid

from accounts.models import GHLAuthCredentials, GHLCustomField
import requests
from decouple import config
from service_app.models import GlobalBasePrice


def resolve_ghl_credentials_for_submission(submission):
    """
    Resolve GHLAuthCredentials for a customer submission.

    Priority: submission.account -> contact.account -> lookup by contact.location_id.
    """
    account = getattr(submission, 'account', None)
    if account is not None:
        return account

    if getattr(submission, 'account_id', None):
        account = GHLAuthCredentials.objects.filter(pk=submission.account_id).first()
        if account:
            return account

    contact = getattr(submission, 'contact', None)
    if contact is None:
        return None

    contact_account = getattr(contact, 'account', None)
    if contact_account is not None:
        return contact_account

    if getattr(contact, 'account_id', None):
        account = GHLAuthCredentials.objects.filter(pk=contact.account_id).first()
        if account:
            return account

    location_id = (getattr(contact, 'location_id', None) or '').strip()
    if location_id:
        return GHLAuthCredentials.objects.filter(location_id=location_id).first()

    return None


def get_global_minimum_base_price_for_submission(submission) -> Decimal:
    """
    Return the account-scoped global minimum quote total for a submission.

    Uses submission.account (or contact/location fallbacks). Returns 0 if no account
    or no GlobalBasePrice row exists for that account.
    """
    account = resolve_ghl_credentials_for_submission(submission)
    if account is None:
        return Decimal('0.00')

    settings = GlobalBasePrice.objects.filter(account=account).first()
    if settings is None:
        return Decimal('0.00')

    return Decimal(settings.base_price or 0)


def resolve_location_id_for_submission(submission, credentials):
    """GHL location id for API calls (contact location preferred, then credentials)."""
    contact = getattr(submission, 'contact', None)
    if contact:
        contact_loc = (getattr(contact, 'location_id', None) or '').strip()
        if contact_loc:
            return contact_loc
    if credentials:
        return (getattr(credentials, 'location_id', None) or '').strip() or None
    return None


def _is_valid_ghl_custom_field_id(ghl_field_id):
    return bool(
        ghl_field_id
        and ghl_field_id != 'ghl_field_id'
        and len(str(ghl_field_id)) >= 5
    )


def format_quote_value_for_ghl(submission):
    """Format submission.final_total for the GHL 'Quote Value' text custom field."""
    total = getattr(submission, 'final_total', None)
    if total is None:
        return None
    return f"{Decimal(total):.2f}"


def append_ghl_custom_field(custom_fields, credentials, field_name, field_value, log_prefix='GHL'):
    """Append a mapped GHL custom field entry to custom_fields if configured for this account."""
    if field_value is None:
        return
    try:
        ghl_field = GHLCustomField.objects.get(
            account=credentials,
            field_name=field_name,
            is_active=True,
        )
        ghl_field.refresh_from_db()
        if _is_valid_ghl_custom_field_id(ghl_field.ghl_field_id):
            custom_fields.append({
                'id': str(ghl_field.ghl_field_id),
                'field_value': str(field_value),
            })
            print(f"✅ [{log_prefix}] Using custom field '{field_name}' with value: {field_value}")
        else:
            print(
                f"⚠️ [{log_prefix}] Invalid ghl_field_id for '{field_name}': "
                f"'{ghl_field.ghl_field_id}'. Skipping."
            )
    except GHLCustomField.DoesNotExist:
        print(f"⚠️ [{log_prefix}] '{field_name}' custom field not found for this account.")
    except Exception as e:
        print(f"❌ [{log_prefix}] Error getting '{field_name}' field: {e}")


def update_ghl_quote_value_for_submission(submission):
    """
    Push submission.final_total to the GHL contact 'Quote Value' custom field.
    Used when a quote is accepted via booking (QuoteSchedule submitted).
    """
    quote_value = format_quote_value_for_ghl(submission)
    if quote_value is None:
        return

    credentials = resolve_ghl_credentials_for_submission(submission)
    if not credentials:
        print('❌ [QUOTE VALUE] No GHLAuthCredentials for submission.')
        return

    contact = getattr(submission, 'contact', None)
    ghl_contact_id = (getattr(contact, 'contact_id', None) or '').strip() if contact else ''
    if not ghl_contact_id:
        print('❌ [QUOTE VALUE] No GHL contact_id on submission contact.')
        return

    custom_fields = []
    append_ghl_custom_field(
        custom_fields,
        credentials,
        'Quote Value',
        quote_value,
        log_prefix='QUOTE VALUE',
    )
    if not custom_fields:
        return

    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {credentials.access_token}',
        'Version': '2021-07-28',
        'Content-Type': 'application/json',
    }
    response = requests.put(
        f'https://services.leadconnectorhq.com/contacts/{ghl_contact_id}',
        json={'customFields': custom_fields},
        headers=headers,
    )
    print(f'⬅️ [QUOTE VALUE] GHL response [{response.status_code}]: {response.text}')


def create_or_update_ghl_contact(submission, is_submit=False):
    try:
        print("🔹 Starting GHL contact sync...")
        credentials = resolve_ghl_credentials_for_submission(submission)
        if not credentials:
            print("❌ No GHLAuthCredentials found for this submission (account or location_id).")
            return

        location_id = resolve_location_id_for_submission(submission, credentials)
        if not location_id:
            print("❌ No location_id available for this submission.")
            return

        token = credentials.access_token
        print(f"✅ Using token (truncated): {token[:10]}..., locationId: {location_id}")

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Version": "2021-07-28",
            "Content-Type": "application/json"
        }

        # Step 1: Determine search URL
        if submission.contact.contact_id:
            search_url = f"https://services.leadconnectorhq.com/contacts/{submission.contact.contact_id}"
            print(f"🔍 Searching by contact_id: {submission.contact.contact_id}")
        else:
            search_query = submission.contact.email or submission.contact.first_name
            if not search_query:
                print("❌ No identifier (email/first_name) to search GHL contact.")
                return
            search_url = f"https://services.leadconnectorhq.com/contacts/?locationId={location_id}&query={search_query}"
            print(f"🔍 Searching by query: {search_query}")

        # Step 2: Fetch existing contact
        print(f"➡️ Sending GET request to {search_url}")
        search_response = requests.get(search_url, headers=headers)
        print(f"⬅️ Response [{search_response.status_code}]: {search_response.text}")

        if search_response.status_code != 200:
            print("❌ Failed to search GHL contact.")
            return

        search_data = search_response.json()
        results = []

        # Handle both cases: list of contacts or single contact
        if "contacts" in search_data and isinstance(search_data["contacts"], list):
            results = search_data["contacts"]
            print(f"📋 Found {len(results)} contacts in search results.")
        elif "contact" in search_data and isinstance(search_data["contact"], dict):
            results = [search_data["contact"]]
            print("📋 Found 1 contact in search results.")
        else:
            print("ℹ️ No contacts found in GHL.")

        # Step 3: Build custom fields
        base_frontend = config('BASE_FRONTEND_URI').rstrip('/')
        booking_url = (
            f"{base_frontend}/booking?"
            f"{urlencode({'submission_id': str(submission.id), 'location_id': location_id})}"
        )
        quote_url = (
            f"{base_frontend}/quote/details/{submission.id}?"
            f"{urlencode({
                'first_name': submission.contact.first_name or '',
                'last_name': submission.contact.last_name or '',
                'phone': submission.contact.phone or '',
                'email': submission.contact.email or '',
                'location_id': location_id,
            })}"
        )
        
        # Get Quote Link custom field using account and field name
        custom_fields = []
        try:
            quote_link_field = GHLCustomField.objects.get(
                account=credentials,
                field_name='Quote Link',
                is_active=True
            )
            quote_link_field.refresh_from_db()
            
            # Validate that we have a real field ID (not a placeholder)
            if quote_link_field.ghl_field_id and quote_link_field.ghl_field_id != 'ghl_field_id' and len(quote_link_field.ghl_field_id) >= 5:
                custom_fields.append({
                    "id": str(quote_link_field.ghl_field_id),
                    "field_value": quote_url if is_submit else booking_url
                })
                print(f"✅ [QUOTE LINK] Using custom field 'Quote Link' with ID: {quote_link_field.ghl_field_id}")
            else:
                print(f"⚠️ [QUOTE LINK] Invalid ghl_field_id value: '{quote_link_field.ghl_field_id}'. Skipping custom field update.")
        except GHLCustomField.DoesNotExist:
            print(f"⚠️ [QUOTE LINK] 'Quote Link' custom field not found for location_id: {location_id}")
        except Exception as e:
            print(f"❌ [QUOTE LINK] Error getting Quote Link field: {str(e)}")

        if is_submit:
            quote_value = format_quote_value_for_ghl(submission)
            if quote_value is not None:
                append_ghl_custom_field(
                    custom_fields,
                    credentials,
                    'Quote Value',
                    quote_value,
                    log_prefix='QUOTE VALUE',
                )
        
        print(f"🛠 Custom fields prepared: {custom_fields}")

        # Step 4: Update or create contact
        if results:
            ghl_contact_id = results[0]["id"]
            tags = results[0].get("tags", [])
            contact_payload = {}

            # Only include customFields if we have fields to update
            if custom_fields:
                contact_payload["customFields"] = custom_fields

            if is_submit:
                if "quote accepted" not in tags:
                    tags.append("quote accepted")
                contact_payload["tags"] = tags
            else:
                if "quoted" not in tags:
                    tags.append("quoted")
                contact_payload["tags"] = tags

            print(f"✏️ Updating contact {ghl_contact_id} with payload: {contact_payload}")
            contact_response = requests.put(
                f"https://services.leadconnectorhq.com/contacts/{ghl_contact_id}",
                json=contact_payload,
                headers=headers
            )
        else:
            contact_payload = {
                "firstName": submission.contact.first_name,
                "email": submission.contact.email,
                "phone": submission.contact.phone,
                "locationId": location_id
            }
            # Only include customFields if we have fields to update
            if custom_fields:
                contact_payload["customFields"] = custom_fields
            print(f" Creating new contact with payload: {contact_payload}")
            contact_response = requests.post(
                "https://services.leadconnectorhq.com/contacts/",
                json=contact_payload,
                headers=headers
            )

        print(f"⬅️ Contact sync response [{contact_response.status_code}]: {contact_response.text}")

        if contact_response.status_code not in [200, 201]:
            print("❌ Failed to create/update contact in GHL.")
            return

        print("✅ Contact synced successfully.")

    except Exception as e:
        print(f"🔥 Error syncing contact: {e}")


def _ghl_headers(access_token):
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28",
        "Content-Type": "application/json",
    }


def _normalize_email(email):
    return (email or "").strip().lower()


def _normalize_phone(phone):
    if not phone:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return digits


def search_ghl_contacts_by_query(credentials, location_id, query):
    """Return list of GHL contact dicts matching query within a location."""
    if not query:
        return []
    headers = _ghl_headers(credentials.access_token)
    response = requests.get(
        "https://services.leadconnectorhq.com/contacts/",
        headers=headers,
        params={"locationId": location_id, "query": query},
        timeout=30,
    )
    if response.status_code != 200:
        print(f"❌ [PUBLIC CONTACT] GHL search failed [{response.status_code}]: {response.text}")
        return []
    data = response.json() or {}
    contacts = data.get("contacts")
    if isinstance(contacts, list):
        return contacts
    if isinstance(data.get("contact"), dict):
        return [data["contact"]]
    return []


def create_or_update_ghl_contact_for_public(credentials, location_id, *, first_name, last_name, email, phone, address=None):
    """
    Create or update a GHL contact for the public quote flow.

    Prefers exact email match within the location; falls back to create.
    Returns the GHL contact id string, or None on failure.
    """
    headers = _ghl_headers(credentials.access_token)
    email_norm = _normalize_email(email)
    phone_norm = (phone or "").strip()

    existing = None
    if email_norm:
        for candidate in search_ghl_contacts_by_query(credentials, location_id, email_norm):
            cand_email = _normalize_email(candidate.get("email"))
            if cand_email and cand_email == email_norm:
                existing = candidate
                break

    if existing is None and phone_norm:
        for candidate in search_ghl_contacts_by_query(credentials, location_id, phone_norm):
            cand_phone = _normalize_phone(candidate.get("phone"))
            if cand_phone and cand_phone == _normalize_phone(phone_norm):
                existing = candidate
                break

    payload = {
        "firstName": (first_name or "").strip(),
        "lastName": (last_name or "").strip(),
        "email": email_norm or None,
        "phone": phone_norm or None,
        "locationId": location_id,
        "tags": ["public quote"],
    }
    if address:
        if address.get("street_address"):
            payload["address1"] = address["street_address"]
        if address.get("city"):
            payload["city"] = address["city"]
        if address.get("state"):
            payload["state"] = address["state"]
        if address.get("postal_code"):
            payload["postalCode"] = address["postal_code"]

    # Drop empty optional fields so GHL does not clear with nulls on update oddly
    payload = {k: v for k, v in payload.items() if v not in (None, "")}

    if existing:
        ghl_id = existing.get("id") or existing.get("_id")
        tags = list(existing.get("tags") or [])
        if "public quote" not in [t.lower() for t in tags if isinstance(t, str)]:
            tags.append("public quote")
        update_payload = {k: v for k, v in payload.items() if k != "locationId"}
        update_payload["tags"] = tags
        response = requests.put(
            f"https://services.leadconnectorhq.com/contacts/{ghl_id}",
            headers=headers,
            json=update_payload,
            timeout=30,
        )
        print(f"⬅️ [PUBLIC CONTACT] Update [{response.status_code}]: {response.text}")
        if response.status_code not in (200, 201):
            return None
        return ghl_id

    response = requests.post(
        "https://services.leadconnectorhq.com/contacts/",
        headers=headers,
        json=payload,
        timeout=30,
    )
    print(f"⬅️ [PUBLIC CONTACT] Create [{response.status_code}]: {response.text}")
    if response.status_code not in (200, 201):
        return None
    body = response.json() or {}
    return (body.get("contact") or {}).get("id") or body.get("id")


def upsert_local_contact_from_public(*, account, location_id, ghl_contact_id, first_name, last_name, email, phone):
    """Create or update the local Contact row for a public quote customer."""
    from django.utils import timezone
    from accounts.models import Contact

    email_norm = _normalize_email(email) or None
    defaults = {
        "account": account,
        "first_name": (first_name or "").strip() or None,
        "last_name": (last_name or "").strip() or None,
        "email": email_norm,
        "phone": (phone or "").strip() or None,
        "location_id": location_id,
        "date_added": timezone.now(),
        "dnd": False,
    }

    contact = None
    if ghl_contact_id:
        contact = Contact.objects.filter(contact_id=ghl_contact_id).first()

    if contact is None and email_norm:
        contact = (
            Contact.objects.filter(account=account, email__iexact=email_norm)
            .order_by("-date_added", "-id")
            .first()
        )

    if contact is None:
        # contact_id is required+unique; use GHL id, or a stable local placeholder until sync
        contact_id = ghl_contact_id or f"public_{uuid.uuid4().hex}"
        contact = Contact.objects.create(contact_id=contact_id, **defaults)
        return contact, True

    for key, value in defaults.items():
        setattr(contact, key, value)
    if ghl_contact_id and contact.contact_id != ghl_contact_id:
        # Prefer real GHL id when we have one and it is not already taken
        if not Contact.objects.filter(contact_id=ghl_contact_id).exclude(pk=contact.pk).exists():
            contact.contact_id = ghl_contact_id
    contact.save()
    return contact, False


def create_local_address_for_contact(contact, address_data):
    """Create a local Address row for the contact (public quote property)."""
    from django.db.models import Max
    from accounts.models import Address

    next_order = (
        Address.objects.filter(contact=contact).aggregate(max_order=Max("order"))["max_order"] or 0
    ) + 1
    fields = {
        "name": (address_data.get("name") or "").strip() or f"Property {next_order}",
        "street_address": (address_data.get("street_address") or "").strip() or None,
        "city": (address_data.get("city") or "").strip() or None,
        "state": (address_data.get("state") or "").strip() or None,
        "postal_code": (address_data.get("postal_code") or "").strip() or None,
        "gate_code": (address_data.get("gate_code") or "").strip() or None,
        "number_of_floors": address_data.get("number_of_floors"),
        "property_sqft": address_data.get("property_sqft"),
        "property_type": address_data.get("property_type") or None,
    }
    return Address.objects.create(
        contact=contact,
        address_id=f"app_{uuid.uuid4().hex[:16]}",
        order=next_order,
        **fields,
    )
