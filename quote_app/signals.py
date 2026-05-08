from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import CustomService, QuoteSchedule
from .quote_schedule_job_sync import sync_job_when_quote_schedule_submitted


@receiver([post_save, post_delete], sender=CustomService)
def update_submission_total(sender, instance, **kwargs):
    """Update the parent submission total whenever custom services change"""
    submission = instance.purchase
    submission.calculate_final_total()


@receiver(post_save, sender=QuoteSchedule)
def handle_quote_submission(sender, instance, created, **kwargs):
    """Create or update an internal job when a quote is submitted/scheduled."""

    if created or not instance.is_submitted:
        return

    sync_job_when_quote_schedule_submitted(instance.submission, instance)
