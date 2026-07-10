"""One-time SSO login tokens for GHL iframe autologin (email + location_id)."""
from __future__ import annotations

import secrets
from typing import Any, Optional

from django.core.cache import cache

SSO_TOKEN_TTL_SECONDS = 120
SSO_CACHE_PREFIX = "sso_login:"


def create_sso_login_token(*, user_id: int, location_id: str, email: str) -> str:
    """Create a single-use token bound to user + location (short TTL)."""
    token = secrets.token_urlsafe(32)
    cache.set(
        f"{SSO_CACHE_PREFIX}{token}",
        {
            "user_id": user_id,
            "location_id": (location_id or "").strip(),
            "email": (email or "").strip().lower(),
        },
        timeout=SSO_TOKEN_TTL_SECONDS,
    )
    return token


def consume_sso_login_token(token: str, *, location_id: str) -> Optional[dict[str, Any]]:
    """
    Validate and consume a one-time SSO token.

    Returns cached payload on success (token is deleted). None if invalid/expired/mismatch.
    """
    token = (token or "").strip()
    location_id = (location_id or "").strip()
    if not token or not location_id:
        return None

    key = f"{SSO_CACHE_PREFIX}{token}"
    payload = cache.get(key)
    if not payload:
        return None

    cache.delete(key)

    if payload.get("location_id") != location_id:
        return None

    return payload
