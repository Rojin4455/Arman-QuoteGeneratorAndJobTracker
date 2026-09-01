"""Serialize GHL account context for login responses (business timezone, not employee)."""
from typing import Optional

from accounts.currency import currency_for_ghl_location
from accounts.models import GHLAuthCredentials, Location as GHLLocation
from accounts.timezone_utils import DEFAULT_ACCOUNT_TIMEZONE
from accounts.user_access import resolve_account_for_user
from service_app.models import User


def serialize_ghl_account(account: Optional[GHLAuthCredentials]) -> Optional[dict]:
    """Return public GHL account fields for a GHLAuthCredentials row."""
    if account is None:
        return None
    tz = (getattr(account, 'timezone', None) or '').strip() or DEFAULT_ACCOUNT_TIMEZONE
    loc_id = (getattr(account, 'location_id', None) or '').strip()
    ghl_location = GHLLocation.objects.filter(pk=loc_id).first() if loc_id else None
    return {
        'id': account.pk,
        'location_id': account.location_id,
        'timezone': tz,
        'currency': currency_for_ghl_location(ghl_location),
        'account_name': getattr(account, 'company_name', None) or '',
    }


def serialize_user_ghl_account(user: User, account: Optional[GHLAuthCredentials] = None) -> Optional[dict]:
    """
    Return public GHL account fields for login/API context.

    Pass ``account`` when iframe ``location_id`` selects the active subaccount.
    """
    if account is None:
        account = getattr(user, 'account', None)
    return serialize_ghl_account(account)


def resolve_login_ghl_account(user: User, location_id: Optional[str] = None) -> Optional[dict]:
    """Account payload for login, honoring optional iframe location_id."""
    account = resolve_account_for_user(user, location_id=location_id)
    return serialize_ghl_account(account)


def serialize_subaccount_office(account: Optional[GHLAuthCredentials]) -> Optional[dict]:
    """Return GHL subaccount business address for map/office context."""
    if account is None:
        return None

    loc_id = (getattr(account, 'location_id', None) or '').strip()
    if not loc_id:
        return None

    ghl_location = GHLLocation.objects.filter(pk=loc_id).first()
    fallback_name = (getattr(account, 'company_name', None) or '').strip() or 'Office'

    if ghl_location is None:
        return {
            'location_id': loc_id,
            'name': fallback_name,
            'full_address': '',
            'address': '',
            'city': '',
            'state': '',
            'postal_code': '',
            'country': '',
        }

    parts = [
        (ghl_location.address or '').strip(),
        (ghl_location.city or '').strip(),
        (ghl_location.state or '').strip(),
        (ghl_location.postal_code or '').strip(),
    ]
    full_address = ', '.join(p for p in parts if p)

    return {
        'location_id': loc_id,
        'name': (ghl_location.name or '').strip() or fallback_name,
        'full_address': full_address,
        'address': ghl_location.address or '',
        'city': ghl_location.city or '',
        'state': ghl_location.state or '',
        'postal_code': ghl_location.postal_code or '',
        'country': ghl_location.country or '',
    }
