"""
Account scoping for multi-tenant / multi-account support.

Resolves the current request's account (GHLAuthCredentials) from:
- Authenticated: request.user.account (or optional override for super admin)
- Unauthenticated: location_id from query params, request body, or X-Location-Id header.
  If no location_id is found, falls back to DEFAULT_LOCATION_ID (for unauthenticated routes).
"""
from typing import Optional
from decouple import config
from accounts.models import GHLAuthCredentials

# Fallback location_id when none is provided in the request (unauthenticated routes).
DEFAULT_LOCATION_ID = config("DEFAULT_LOCATION_ID")


def _get_location_id_from_request(request) -> Optional[str]:
    """Get location_id from query params, body, or X-Location-Id header; else DEFAULT_LOCATION_ID."""
    location_id = request.query_params.get("location_id")
    if location_id:
        return location_id
    if hasattr(request, "data") and isinstance(getattr(request, "data", None), dict):
        location_id = request.data.get("location_id")
        if location_id:
            return location_id
    location_id = request.META.get("HTTP_X_LOCATION_ID")
    if location_id:
        return location_id
    return DEFAULT_LOCATION_ID


def get_account_from_request(request, allow_superadmin_override: bool = True) -> Optional[GHLAuthCredentials]:
    """
    Resolve the account for this request and set request.account.

    - Authenticated: uses request.user.account. If allow_superadmin_override and
      user is superuser, can override via query/body account_id or location_id.
    - Unauthenticated: uses location_id from query params, body, or X-Location-Id header.

    Returns the resolved account or None if not found / not allowed.
    Sets request.account when an account is resolved.
    """
    account = None

    if request.user and getattr(request.user, "is_authenticated", False):
        # Super admin override: optional account_id or location_id to act on another account
        if allow_superadmin_override and getattr(request.user, "is_superuser", False):
            account_id = request.query_params.get("account_id") or (
                request.data.get("account_id") if hasattr(request, "data") and isinstance(getattr(request, "data", None), dict) else None
            )
            if account_id:
                try:
                    account = GHLAuthCredentials.objects.get(pk=account_id)
                except (GHLAuthCredentials.DoesNotExist, ValueError):
                    pass
            if account is None:
                loc_id = _get_location_id_from_request(request)
                if loc_id:
                    account = GHLAuthCredentials.objects.filter(location_id=loc_id).first()
        if account is None:
            account = getattr(request.user, "account", None)
    else:
        # Unauthenticated: require location_id
        location_id = _get_location_id_from_request(request)
        if location_id:
            account = GHLAuthCredentials.objects.filter(location_id=location_id).first()

    if account is not None:
        request.account = account
    return account
