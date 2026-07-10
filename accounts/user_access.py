"""Authorization helpers for iframe location context."""
from typing import Optional

from accounts.models import GHLAuthCredentials
from service_app.models import User


def user_can_access_location(user: User, location_id: str) -> bool:
    """
    Return True if the user may operate in the given GHL location (subaccount).

    Account users may only access their linked account's location_id.
    """
    location_id = (location_id or "").strip()
    if not user or not location_id:
        return False
    if getattr(user, "is_superuser", False):
        return GHLAuthCredentials.objects.filter(location_id=location_id, is_active=True).exists()

    account = getattr(user, "account", None)
    return bool(account and account.location_id == location_id)


def resolve_account_for_user(
    user: User,
    location_id: Optional[str] = None,
) -> Optional[GHLAuthCredentials]:
    """
    Resolve the active GHL account for login or API context.

    When location_id is provided, returns that account if the user may access it.
    Otherwise returns the user's linked account.
    """
    location_id = (location_id or "").strip()
    if location_id:
        account = GHLAuthCredentials.objects.filter(location_id=location_id, is_active=True).first()
        if account and user_can_access_location(user, location_id):
            return account
        return None

    return getattr(user, "account", None)
