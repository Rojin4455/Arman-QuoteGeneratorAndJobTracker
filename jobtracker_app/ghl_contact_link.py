import re
import uuid

import requests
from django.db.models import Q

from accounts.models import Contact, GHLAuthCredentials
from accounts.utils import create_or_update_contact

GHL_BASE_URL = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

FOUND = "found"
MISSING = "missing"
UNKNOWN = "unknown"


def is_placeholder_ghl_contact_id(value, location_id=None):
    """IDs that can never exist in GHL: CSV-import UUIDs, public quote placeholders, the location id."""
    value = (value or "").strip()
    if not value:
        return False
    if value.startswith("public_") or (location_id and value == location_id):
        return True
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _phone_digits(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _headers(credentials):
    return {
        "Authorization": f"Bearer {credentials.access_token}",
        "Version": GHL_API_VERSION,
        "Accept": "application/json",
    }


def fetch_ghl_contact(credentials, contact_id):
    """Return (FOUND, contact) / (MISSING, None) / (UNKNOWN, None) when GHL could not be checked."""
    try:
        response = requests.get(
            f"{GHL_BASE_URL}/contacts/{contact_id}", headers=_headers(credentials), timeout=30
        )
    except requests.RequestException:
        return UNKNOWN, None
    if response.status_code == 200:
        return FOUND, (response.json() or {}).get("contact")
    if response.status_code in (400, 404):
        return MISSING, None
    return UNKNOWN, None


def _search_ghl_contacts(credentials, query):
    try:
        response = requests.get(
            f"{GHL_BASE_URL}/contacts/",
            headers=_headers(credentials),
            params={"query": query, "locationId": credentials.location_id},
            timeout=30,
        )
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    return response.json().get("contacts") or []


def search_ghl_contact_by_email_or_phone(credentials, emails, phones):
    """
    Exact email match first, then phone (last 10 digits).
    A phone match is only accepted when exactly one GHL contact has that number.
    Returns (FOUND, contact, matched_by) / (MISSING, None, None) / (UNKNOWN, None, None).
    """
    could_not_check = False
    for email in emails:
        results = _search_ghl_contacts(credentials, email)
        if results is None:
            could_not_check = True
            continue
        matches = [c for c in results if (c.get("email") or "").strip().lower() == email]
        if matches:
            return FOUND, matches[0], f"email {email}"

    for phone in phones:
        results = _search_ghl_contacts(credentials, phone)
        if results is None:
            could_not_check = True
            continue
        matches = [c for c in results if _phone_digits(c.get("phone")) == phone]
        if len(matches) == 1:
            return FOUND, matches[0], f"phone {phone}"

    if could_not_check:
        return UNKNOWN, None, None
    return MISSING, None, None


def job_contact_candidates(job):
    """Current GHL id plus the emails/phones we can search GHL with, from the job and its contacts."""
    contacts = [c for c in (job.contact, getattr(job.submission, "contact", None)) if c]
    current_id = (job.ghl_contact_id or "").strip()
    if not current_id:
        current_id = next((c.contact_id.strip() for c in contacts if (c.contact_id or "").strip()), "")

    emails, phones = [], []
    for email in [job.customer_email] + [c.email for c in contacts]:
        email = (email or "").strip().lower()
        if email and email not in emails:
            emails.append(email)
    for phone in [job.customer_phone] + [c.phone for c in contacts]:
        phone = _phone_digits(phone)
        if phone and phone not in phones:
            phones.append(phone)
    return current_id, emails, phones


def diagnose_job_contact(job, credentials):
    """
    Returns a dict:
      status: "ok" | "relinkable" | "missing" | "unknown"
      current_id, ghl_contact (dict, when relinkable), matched_by, reason
    """
    current_id, emails, phones = job_contact_candidates(job)
    location_id = credentials.location_id

    reason = "no GHL contact id"
    if current_id:
        if is_placeholder_ghl_contact_id(current_id, location_id):
            reason = "placeholder id"
        else:
            status, _ = fetch_ghl_contact(credentials, current_id)
            if status == FOUND:
                return {"status": "ok", "current_id": current_id}
            if status == UNKNOWN:
                return {"status": "unknown", "current_id": current_id, "reason": "GHL lookup failed"}
            reason = "id not found in GHL"

    status, ghl_contact, matched_by = search_ghl_contact_by_email_or_phone(credentials, emails, phones)
    if status == FOUND:
        return {
            "status": "relinkable",
            "current_id": current_id,
            "ghl_contact": ghl_contact,
            "matched_by": matched_by,
            "reason": reason,
        }
    if status == UNKNOWN:
        return {"status": "unknown", "current_id": current_id, "reason": f"{reason}; GHL search failed"}
    return {
        "status": "missing",
        "current_id": current_id,
        "reason": f"{reason}; nothing in GHL for {', '.join(emails + phones) or 'no email/phone'}",
    }


def relink_jobs_to_ghl_contact(job, ghl_contact, location_id):
    """Upsert the GHL contact locally and point this job plus its not-yet-invoiced series jobs at it."""
    local = create_or_update_contact({"contact": ghl_contact, "locationId": location_id})
    if not local:
        local = Contact.objects.filter(contact_id=ghl_contact.get("id")).first()
    if not local:
        return None, 0

    scope = Q(id=job.id)
    if job.series_id:
        uninvoiced = (Q(invoice_id__isnull=True) | Q(invoice_id="")) & (
            Q(invoice_url__isnull=True) | Q(invoice_url="")
        )
        scope |= Q(series_id=job.series_id) & uninvoiced

    updates = {"contact": local, "ghl_contact_id": local.contact_id}
    if local.email:
        updates["customer_email"] = local.email
    from jobtracker_app.models import Job

    count = Job.objects.filter(scope).update(**updates)
    return local, count


def ensure_job_ghl_contact(job):
    """
    Make sure the job points at a contact that exists in GHL, relinking by email/phone if needed.
    Returns (ghl_contact_id or None, message).
    """
    location_id = (job.account.location_id if job.account else None) or (
        job.contact.location_id if job.contact else None
    )
    credentials = (
        GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if location_id
        else GHLAuthCredentials.objects.first()
    )
    if not credentials or not credentials.access_token:
        current_id, _, _ = job_contact_candidates(job)
        return current_id or None, "no GHL credentials; contact not verified"

    result = diagnose_job_contact(job, credentials)
    if result["status"] in ("ok", "unknown"):
        return result["current_id"] or None, result.get("reason", "ok")
    if result["status"] == "missing":
        return None, result["reason"]

    local, count = relink_jobs_to_ghl_contact(job, result["ghl_contact"], credentials.location_id)
    if not local:
        return None, f"found GHL contact by {result['matched_by']} but could not save it locally"
    job.refresh_from_db()
    return local.contact_id, (
        f"relinked {count} job(s) from {result['current_id'] or 'no id'} to {local.contact_id} "
        f"(matched by {result['matched_by']})"
    )
