"""Celery tasks for the referral program."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def backfill_referral_links(location_id: str = "") -> dict:
    """
    Ensure every existing contact has a unique referral link.
    Optionally limit to one subaccount by location_id.
    """
    from accounts.models import Contact, GHLAuthCredentials
    from referral_app.models import ReferralLink
    from referral_app import services

    accounts = GHLAuthCredentials.objects.filter(is_active=True)
    if location_id:
        accounts = accounts.filter(location_id=location_id)

    created = 0
    skipped = 0
    errors = 0
    for account in accounts:
        existing_contact_ids = set(
            ReferralLink.objects.filter(account=account).values_list("contact_id", flat=True)
        )
        contacts = Contact.objects.filter(account=account).exclude(pk__in=existing_contact_ids)
        for contact in contacts.iterator(chunk_size=500):
            try:
                services.ensure_referral_link(account, contact)
                created += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "backfill_referral_links: failed contact %s (%s): %s",
                    contact.pk,
                    account.location_id,
                    exc,
                )
        skipped += len(existing_contact_ids)

    result = {"created": created, "already_had": skipped, "errors": errors}
    logger.info("backfill_referral_links: %s", result)
    return result
