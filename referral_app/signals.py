"""Referral lifecycle signals: auto link on contact create, discount attach/release on jobs."""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="accounts.Contact", dispatch_uid="referral_link_on_contact_create")
def create_referral_link_for_new_contact(sender, instance, created, raw=False, **kwargs):
    """Every customer gets a unique referral link as soon as their contact exists."""
    if raw or not created:
        return
    if not instance.account_id:
        return
    try:
        from referral_app import services

        services.ensure_referral_link(instance.account, instance)
    except Exception as exc:
        logger.warning("Referral link auto-create failed for contact %s: %s", instance.pk, exc)


@receiver(post_save, sender="jobtracker_app.Job", dispatch_uid="referral_discount_on_job_save")
def sync_referral_discount_on_job_save(sender, instance, created, raw=False, **kwargs):
    """
    Attach the pending referral discount to the referred customer's first job,
    and release it if the job is cancelled before payment.
    """
    if raw:
        return
    try:
        from referral_app import services

        if instance.status == "cancelled":
            services.release_referral_discount_from_job(instance)
            return
        # Attach even when the contact FK is missing — the service resolves the
        # customer via ghl_contact_id / customer_email as a fallback.
        if not instance.referral_attribution_id and (
            instance.contact_id or instance.ghl_contact_id or instance.customer_email
        ):
            services.attach_referral_discount_to_job(instance)
    except Exception as exc:
        logger.warning("Referral job discount sync failed for job %s: %s", instance.pk, exc)
