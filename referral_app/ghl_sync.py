"""GHL custom field + contact updates for referral share URLs and program amounts."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from accounts.models import GHLAuthCredentials, GHLCustomField, Contact

logger = logging.getLogger(__name__)

REFERRAL_LINK_FIELD_NAME = "Referral Link"
REFERRAL_REWARD_FIELD_NAME = "Referral Reward"
FRIEND_DISCOUNT_FIELD_NAME = "Friend Discount"
REFERRAL_MINIMUM_FIELD_NAME = "Referral Minimum"
REFERRAL_INVITE_TAG = "referral invite"
GHL_API_VERSION = "2021-07-28"
GHL_CUSTOM_FIELDS_URL = "https://services.leadconnectorhq.com/locations/{location_id}/customFields"
GHL_CONTACT_URL = "https://services.leadconnectorhq.com/contacts/{contact_id}"

REFERRAL_CONTACT_FIELD_SPECS = (
    {
        "name": REFERRAL_LINK_FIELD_NAME,
        "field_type": "url",
        "description": "Personal customer referral share URL",
    },
    {
        "name": REFERRAL_REWARD_FIELD_NAME,
        "field_type": "text",
        "description": "Referrer credit amount, e.g. $50",
    },
    {
        "name": FRIEND_DISCOUNT_FIELD_NAME,
        "field_type": "text",
        "description": "Friend discount amount, e.g. $50",
    },
    {
        "name": REFERRAL_MINIMUM_FIELD_NAME,
        "field_type": "text",
        "description": "Minimum qualifying invoice, e.g. $150",
    },
)


def _headers(access_token: str) -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }


def format_money_label(cents: int) -> str:
    """Format cents as a dollar label for GHL messages, e.g. 5000 → '$50'."""
    amount = int(cents or 0)
    if amount % 100 == 0:
        return f"${amount // 100}"
    return f"${amount / 100:.2f}"


def get_mapped_custom_field(account: GHLAuthCredentials, field_name: str) -> Optional[GHLCustomField]:
    return (
        GHLCustomField.objects.filter(
            account=account,
            field_name__iexact=field_name,
            is_active=True,
        )
        .order_by("-updated_at", "-id")
        .first()
    )


def get_referral_link_field(account: GHLAuthCredentials) -> Optional[GHLCustomField]:
    return get_mapped_custom_field(account, REFERRAL_LINK_FIELD_NAME)


def _fetch_remote_fields(account: GHLAuthCredentials) -> dict:
    location_id = (account.location_id or "").strip()
    token = (account.access_token or "").strip()
    if not location_id or not token:
        return {}
    from accounts.utils import fetch_location_custom_fields

    try:
        return fetch_location_custom_fields(location_id, token) or {}
    except Exception as exc:
        logger.warning("Failed to list GHL custom fields for %s: %s", location_id, exc)
        return {}


def _map_existing_remote_field(
    account: GHLAuthCredentials,
    field_name: str,
    field_type: str,
    description: str,
    remote: dict,
) -> Optional[GHLCustomField]:
    for field_id, info in remote.items():
        name = (info.get("name") or "").strip()
        if name.casefold() != field_name.casefold():
            continue
        field, _ = GHLCustomField.objects.update_or_create(
            account=account,
            ghl_field_id=field_id,
            defaults={
                "field_name": field_name,
                "field_type": field_type,
                "is_active": True,
                "description": description,
            },
        )
        return field
    return None


def ensure_contact_custom_field(
    account: GHLAuthCredentials,
    field_name: str,
    field_type: str = "text",
    description: str = "",
    remote: Optional[dict] = None,
) -> Optional[GHLCustomField]:
    existing = get_mapped_custom_field(account, field_name)
    if existing and existing.ghl_field_id:
        return existing

    location_id = (account.location_id or "").strip()
    token = (account.access_token or "").strip()
    if not location_id or not token:
        logger.warning(
            "Cannot ensure GHL field '%s': missing location_id/token account=%s",
            field_name,
            account.pk,
        )
        return None

    if remote is None:
        remote = _fetch_remote_fields(account)

    mapped = _map_existing_remote_field(account, field_name, field_type, description, remote)
    if mapped:
        return mapped

    try:
        response = requests.post(
            GHL_CUSTOM_FIELDS_URL.format(location_id=location_id),
            headers=_headers(token),
            json={
                "name": field_name,
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
                "field_name": field_name,
                "field_type": field_type,
                "is_active": True,
                "description": description,
            },
        )
        logger.info("Created GHL custom field '%s' id=%s location=%s", field_name, field_id, location_id)
        return field
    except Exception as exc:
        logger.warning("Failed to create GHL field '%s' for %s: %s", field_name, location_id, exc)
        return None


def ensure_referral_invite_custom_fields(account: GHLAuthCredentials) -> dict[str, GHLCustomField]:
    """Create/map Referral Link + program amount fields on this location."""
    remote = None
    mapped: dict[str, GHLCustomField] = {}
    for spec in REFERRAL_CONTACT_FIELD_SPECS:
        existing = get_mapped_custom_field(account, spec["name"])
        if existing and existing.ghl_field_id:
            mapped[spec["name"]] = existing
            continue
        if remote is None:
            remote = _fetch_remote_fields(account)
        field = ensure_contact_custom_field(
            account,
            spec["name"],
            field_type=spec["field_type"],
            description=spec["description"],
            remote=remote,
        )
        if field:
            mapped[spec["name"]] = field
    return mapped


def ensure_referral_link_custom_field(account: GHLAuthCredentials) -> Optional[GHLCustomField]:
    fields = ensure_referral_invite_custom_fields(account)
    return fields.get(REFERRAL_LINK_FIELD_NAME)


def ensure_referral_link_custom_field_for_all_accounts() -> dict:
    """Create/sync referral contact custom fields on every active onboarded location."""
    summary = {"ok": [], "failed": [], "skipped": []}
    accounts = GHLAuthCredentials.objects.filter(is_active=True).exclude(location_id__isnull=True)
    for account in accounts:
        if not (account.location_id or "").strip() or not (account.access_token or "").strip():
            summary["skipped"].append(account.location_id or str(account.pk))
            continue
        fields = ensure_referral_invite_custom_fields(account)
        if fields.get(REFERRAL_LINK_FIELD_NAME):
            summary["ok"].append(account.location_id)
        else:
            summary["failed"].append(account.location_id)
    return summary


def build_referral_contact_field_values(account: GHLAuthCredentials, share_url: str = "") -> dict[str, str]:
    from referral_app.models import ReferralProgram

    program, _ = ReferralProgram.objects.get_or_create(account=account)
    values = {
        REFERRAL_REWARD_FIELD_NAME: format_money_label(program.referrer_reward_cents),
        FRIEND_DISCOUNT_FIELD_NAME: format_money_label(program.friend_reward_cents),
        REFERRAL_MINIMUM_FIELD_NAME: format_money_label(program.minimum_invoice_cents),
    }
    url = (share_url or "").strip()
    if url:
        values[REFERRAL_LINK_FIELD_NAME] = url
    return values


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
    Write referral URL + current program amounts onto the GHL contact, then add
    tag 'referral invite'. Amounts come from JobTracker program settings so
    email/SMS merge fields stay in sync. Tag is not gated on the field write.
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

    mapped = ensure_referral_invite_custom_fields(account)
    values = build_referral_contact_field_values(account, share_url)
    custom_fields = []
    for spec in REFERRAL_CONTACT_FIELD_SPECS:
        field = mapped.get(spec["name"])
        value = values.get(spec["name"])
        if not field or not field.ghl_field_id or value is None:
            continue
        custom_fields.append({"id": str(field.ghl_field_id), "field_value": value})

    if custom_fields:
        try:
            put_resp = requests.put(
                get_url,
                headers=headers,
                json={"customFields": custom_fields},
                timeout=20,
            )
            if put_resp.status_code not in (200, 201):
                logger.warning(
                    "GHL referral custom field update failed for %s: %s %s",
                    ghl_id,
                    put_resp.status_code,
                    put_resp.text[:300],
                )
        except Exception as exc:
            logger.warning("GHL referral custom field update failed for %s: %s", ghl_id, exc)
    else:
        logger.warning(
            "Referral custom fields not available for contact %s; still adding invite tag",
            contact.pk,
        )

    return _add_referral_invite_tag(
        contact,
        tags=tags,
        headers=headers,
        get_url=get_url,
        ghl_id=ghl_id,
    )
