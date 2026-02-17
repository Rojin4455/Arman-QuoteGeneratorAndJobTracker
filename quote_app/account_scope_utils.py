"""
Helpers for account-scoped access in quote_app.

Use these for nested resources: load the parent (e.g. CustomerSubmission),
ensure it belongs to request.account, then use its related data.
"""
from django.shortcuts import get_object_or_404

from accounts.models import GHLAuthCredentials
from .models import CustomerSubmission


def get_submission_for_account(submission_id, account: GHLAuthCredentials):
    """Return CustomerSubmission if it belongs to account; otherwise 404."""
    submission = get_object_or_404(CustomerSubmission, pk=submission_id)
    if account is None or submission.account_id != account.id:
        from rest_framework.exceptions import NotFound
        raise NotFound("Submission not found.")
    return submission
