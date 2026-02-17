"""
Helpers for account-scoped access in service_app.

Use these for nested resources (e.g. questions for a service): load the parent,
ensure it belongs to request.account, then use its related data.
"""
from django.shortcuts import get_object_or_404

from accounts.models import GHLAuthCredentials
from .models import Service, Location, GlobalBasePrice, GlobalSizePackage


def get_service_for_account(service_id, account: GHLAuthCredentials):
    """Return Service if it belongs to account; otherwise 404."""
    service = get_object_or_404(Service, pk=service_id)
    if account is None or service.account_id != account.id:
        from rest_framework.exceptions import NotFound
        raise NotFound("Service not found.")
    return service


def get_location_for_account(location_id, account: GHLAuthCredentials):
    """Return Location if it belongs to account; otherwise 404."""
    location = get_object_or_404(Location, pk=location_id)
    if account is None or location.account_id != account.id:
        from rest_framework.exceptions import NotFound
        raise NotFound("Location not found.")
    return location


def get_global_size_package_for_account(package_id, account: GHLAuthCredentials):
    """Return GlobalSizePackage if it belongs to account; otherwise 404."""
    obj = get_object_or_404(GlobalSizePackage, pk=package_id)
    if account is None or obj.account_id != account.id:
        from rest_framework.exceptions import NotFound
        raise NotFound("Global size package not found.")
    return obj
