"""
Helpers for account-scoped access in quote_app.

Use these for nested resources: load the parent (e.g. CustomerSubmission),
ensure it belongs to request.account, then use its related data.
"""
from django.shortcuts import get_object_or_404

from accounts.models import GHLAuthCredentials
from .models import CustomerSubmission


def job_belongs_to_account(job, account: GHLAuthCredentials | None) -> bool:
    """True if job is visible for account-scoped quote/job APIs."""
    if account is None:
        return False
    if getattr(job, "account_id", None) == account.id:
        return True
    if not getattr(job, "account_id", None) and getattr(job, "submission_id", None):
        sub = getattr(job, "submission", None)
        if sub is not None and getattr(sub, "account_id", None) == account.id:
            return True
    return False


def get_job_for_account(job_id, account: GHLAuthCredentials):
    """Return Job if it belongs to account (404 otherwise)."""
    from jobtracker_app.models import Job
    from rest_framework.exceptions import NotFound

    job = get_object_or_404(Job, pk=job_id)
    if not job_belongs_to_account(job, account):
        raise NotFound("Job not found.")
    return job


def get_submission_for_account(submission_id, account: GHLAuthCredentials):
    """Return CustomerSubmission if it belongs to account; otherwise 404."""
    submission = get_object_or_404(CustomerSubmission, pk=submission_id)
    if account is None or submission.account_id != account.id:
        from rest_framework.exceptions import NotFound
        raise NotFound("Submission not found.")
    return submission
