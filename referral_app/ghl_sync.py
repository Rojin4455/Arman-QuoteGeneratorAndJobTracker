"""GHL custom field + contact updates for referral share URLs."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from accounts.models import GHLAuthCredentials, GHLCustomField, Contact

logger = logging.getLogger(__name__)

REFERRAL_LINK_FIELD_NAME = "Referral Link"
REFERRAL_INVITE_TAG = "referral invite"
GHL_API_VERSION = "2021-07-28"
GHL_CUSTOM_FIELDS_URL = "https://services.leadconnectorhq.com/locations/{location_id}/customFields"
GHL_CONTACT_URL = "https://services.leadconnectorhq.com/contacts/{contact_id}"


def _headers(access_token: str) -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }


def get_referral_link_field(account: GHLAuthCredentials) -> Optional[GHLCustomField]:
    return (
        GHLCustomField.objects.filter(
            account=account,
            field_name__iexact=REFERRAL_LINK_FIELD_NAME,
            is_active=True,
        )
        .order_by("-updated_at", "-id")
        .first()
    )


def ensure_referral_link_custom_field(account: GHLAuthCredentials) -> Optional[GHLCustomField]:
    """
    Make sure this subaccount has a contact custom field named 'Referral Link'.

    Reuses an existing GHL field of that name, otherwise creates one, then stores
    the mapping in GHLCustomField.
    """
    existing = get_referral_link_field(account)
    if existing and existing.ghl_field_id:
        return existing

    location_id = (account.location_id or "").strip()
    token = (account.access_token or "").strip()
    if not location_id or not token:
        logger.warning(
            "Cannot ensure Referral Link field: missing location_id/token account=%s",
            account.pk,
        )
        return None

    from accounts.utils import fetch_location_custom_fields

    try:
        remote = fetch_location_custom_fields(location_id, token)
    except Exception as exc:
        logger.warning("Failed to list GHL custom fields for %s: %s", location_id, exc)
        remote = {}

    for field_id, info in remote.items():
        name = (info.get("name") or "").strip()
        if name.casefold() != REFERRAL_LINK_FIELD_NAME.casefold():
            continue
        field, _ = GHLCustomField.objects.update_or_create(
            account=account,
            ghl_field_id=field_id,
            defaults={
                "field_name": REFERRAL_LINK_FIELD_NAME,
                "field_type": "url",
                "is_active": True,
                "description": "Personal customer referral share URL",
            },
        )
        return field

    try:
        response = requests.post(
            GHL_CUSTOM_FIELDS_URL.format(location_id=location_id),
            headers=_headers(token),
            json={
                "name": REFERRAL_LINK_FIELD_NAME,
                "dataType": "TEXT",
                "model": "contact",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        created = payload.get("customField") or payload
        field_id = created.get("id")
        if not field_id:
            raise ValueError(f"GHL create custom field missing id: {payload}")
        field, _ = GHLCustomField.objects.update_or_create(
            account=account,
            ghl_field_id=field_id,
            defaults={
                "field_name": REFERRAL_LINK_FIELD_NAME,
                "field_type": "url",
                "is_active": True,
                "description": "Personal customer referral share URL",
            },
        )
        logger.info("Created GHL custom field '%s' id=%s location=%s", REFERRAL_LINK_FIELD_NAME, field_id, location_id)
        return field
    except Exception as exc:
        logger.warning("Failed to create Referral Link field for %s: %s", location_id, exc)
        return None


def ensure_referral_link_custom_field_for_all_accounts() -> dict:
    """Create/sync the Referral Link field on every active onboarded location."""
    summary = {"ok": [], "failed": [], "skipped": []}
    accounts = GHLAuthCredentials.objects.filter(is_active=True).exclude(location_id__isnull=True)
    for account in accounts:
        if not (account.location_id or "").strip() or not (account.access_token or "").strip():
            summary["skipped"].append(account.location_id or str(account.pk))
            continue
        field = ensure_referral_link_custom_field(account)
        if field:
            summary["ok"].append(account.location_id)
        else:
            summary["failed"].append(account.location_id)
    return summary


def _add_referral_invite_tag(
    contact: Contact,
    *,
    tags: list,
    headers: dict,
    get_url: str,
    ghl_id: str,
) -> bool:
    """Add 'referral invite' on the GHL contact. Does not require a referral link."""
    lower = {str(t).lower() for t in tags if isinstance(t, str)}
    if REFERRAL_INVITE_TAG.lower() in lower:
        return True
    merged = list(tags) + [REFERRAL_INVITE_TAG]
    try:
        tag_resp = requests.put(get_url, headers=headers, json={"tags": merged}, timeout=20)
        if tag_resp.status_code in (200, 201):
            contact.tags = merged
            contact.save(update_fields=["tags"])
            return True
        logger.warning(
            "GHL referral invite tag failed for %s: %s",
            ghl_id,
            tag_resp.status_code,
        )
        return False
    except Exception as exc:
        logger.warning("GHL referral invite tag failed for %s: %s", ghl_id, exc)
        return False


def push_referral_link_and_invite_tag(
    contact: Contact,
    account: GHLAuthCredentials,
    share_url: str = "",
) -> bool:
    """
    Add tag 'referral invite' on job-complete invite.

    Existing customers get the tag even if they never generated a referral
    link. Writing the share URL into GHL 'Referral Link' is best-effort and
    must not block the tag.
    """
    ghl_id = (contact.contact_id or "").strip()
    if not ghl_id or ghl_id.startswith("public_"):
        return False
    token = (account.access_token or "").strip()
    if not token:
        return False

    headers = _headers(token)
    get_url = GHL_CONTACT_URL.format(contact_id=ghl_id)
    tags = list(contact.tags or []) if isinstance(contact.tags, list) else []
    try:
        resp = requests.get(get_url, headers=headers, timeout=20)
        if resp.status_code == 200:
            body = resp.json().get("contact") or resp.json()
            tags = list(body.get("tags") or [])
    except Exception as exc:
        logger.warning("GHL contact GET failed for %s: %s", ghl_id, exc)

    if share_url:
        field = ensure_referral_link_custom_field(account)
        if field and field.ghl_field_id:
            payload = {
                "customFields": [
                    {"id": str(field.ghl_field_id), "field_value": share_url},
                ]
            }
            try:
                put_resp = requests.put(get_url, headers=headers, json=payload, timeout=20)
                if put_resp.status_code not in (200, 201):
                    logger.warning(
                        "GHL Referral Link field update failed for %s: %s %s",
                        ghl_id,
                        put_resp.status_code,
                        put_resp.text[:300],
                    )
            except Exception as exc:
                logger.warning("GHL Referral Link field update failed for %s: %s", ghl_id, exc)
        else:
            logger.warning(
                "Referral Link field not available for contact %s; still adding invite tag",
                contact.pk,
            )

    return _add_referral_invite_tag(
        contact,
        tags=tags,
        headers=headers,
        get_url=get_url,
        ghl_id=ghl_id,
    )
