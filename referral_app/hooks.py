"""Hooks that wire referral qualification into JobTracker invoice/job events."""
from __future__ import annotations

import logging

from typing import Optional

from accounts.models import Contact, GHLAuthCredentials
from referral_app.money import dollars_to_cents
from referral_app import services

logger = logging.getLogger(__name__)


def resolve_contact_for_job(job) -> Optional[Contact]:
    """
    Resolve the job's customer Contact. Prefers the FK; falls back to the GHL
    contact id and then the customer email so referral logic still works for
    jobs whose contact link was lost.
    """
    contact = getattr(job, "contact", None)
    if contact:
        return contact
    account = getattr(job, "account", None)
    if not account:
        return None
    ghl_id = (getattr(job, "ghl_contact_id", None) or "").strip()
    if ghl_id:
        contact = Contact.objects.filter(account=account, contact_id=ghl_id).first()
        if contact:
            return contact
    email = (getattr(job, "customer_email", None) or "").strip()
    if email:
        return (
            Contact.objects.filter(account=account, email__iexact=email)
            .order_by("-id")
            .first()
        )
    return None


def _resolve_contact_for_invoice(account: GHLAuthCredentials, invoice) -> Optional[Contact]:
    contact_id = (getattr(invoice, "contact_id", None) or "").strip()
    if contact_id:
        contact = Contact.objects.filter(account=account, contact_id=contact_id).first()
        if contact:
            return contact
    email = (getattr(invoice, "contact_email", None) or "").strip()
    if email:
        return (
            Contact.objects.filter(account=account, email__iexact=email)
            .order_by("-id")
            .first()
        )
    return None


def on_invoice_paid(*, location_id: str, invoice_id: str) -> None:
    """
    Called after the GHL InvoicePaid webhook updates the local invoice status.

    Reward policy: the referrer is credited only on FULL payment. GHL emits
    InvoicePaid only when an invoice is fully paid (partial payments emit
    InvoicePartiallyPaid, which never reaches this hook). As a belt-and-braces
    guard we also skip when the local invoice clearly still has a balance due.
    """
    try:
        from dashboard_app.models import Invoice
        from jobtracker_app.models import Job

        account = GHLAuthCredentials.objects.filter(location_id=location_id, is_active=True).first()
        if not account:
            return

        invoice = Invoice.objects.filter(account=account, invoice_id=invoice_id).first()
        if not invoice:
            invoice = Invoice.objects.filter(location_id=location_id, invoice_id=invoice_id).first()
        if not invoice:
            return

        if invoice.status not in ("paid",):
            logger.info(
                "Referral: invoice %s status=%s, not fully paid — skipping reward",
                invoice_id,
                invoice.status,
            )
            return
        # Stale-safe guard: only block when local amounts affirmatively show a
        # remaining balance despite amount_paid being tracked.
        if (invoice.amount_paid or 0) > 0 and (invoice.amount_due or 0) > 0:
            logger.info(
                "Referral: invoice %s has amount_due=%s — treating as partial, skipping",
                invoice_id,
                invoice.amount_due,
            )
            return

        contact = _resolve_contact_for_invoice(account, invoice)
        if not contact:
            logger.info("Referral: no contact for paid invoice %s", invoice_id)
            return

        invoice_cents = dollars_to_cents(invoice.sub_total or invoice.total or 0)
        job = Job.objects.filter(account=account, invoice_id=invoice_id).first()
        job_id = job.id if job else None

        result = services.qualify_referral_on_invoice_paid(
            account=account,
            contact=contact,
            invoice_id=invoice_id,
            invoice_cents=invoice_cents,
            job_id=job_id,
        )
        logger.info("Referral qualify invoice.paid %s → %s", invoice_id, result)
    except Exception as exc:
        logger.exception("Referral on_invoice_paid failed for %s: %s", invoice_id, exc)


def on_invoice_void_or_refund(*, location_id: str, invoice_id: str, reason: str = "voided") -> None:
    try:
        account = GHLAuthCredentials.objects.filter(location_id=location_id, is_active=True).first()
        if not account:
            return
        result = services.reverse_invoice_credit_application(
            account=account,
            invoice_id=invoice_id,
            reason=reason,
        )
        logger.info("Referral reverse invoice %s → %s", invoice_id, result)
    except Exception as exc:
        logger.exception("Referral reverse failed for %s: %s", invoice_id, exc)


def on_job_completed(job) -> None:
    try:
        result = services.handle_job_completed_invitation(job)
        logger.info("Referral job.completed %s → %s", getattr(job, "id", None), result)
    except Exception as exc:
        logger.exception("Referral on_job_completed failed: %s", exc)


def apply_credit_before_invoice_create(job) -> int:
    """
    Apply available referral WALLET credit onto the job right before GHL invoice
    creation. Stored in job.referral_credit_amount (never touches the manual
    discount fields). Ledger debit keyed by job id — idempotent.
    """
    try:
        from jobtracker_app.models import Job
        from referral_app.models import CustomerCreditLedger
        from referral_app.money import cents_to_dollars, dollars_to_cents

        account = getattr(job, "account", None)
        contact = resolve_contact_for_job(job)
        if not account or not contact:
            return 0

        idem_key = f"application:job:{job.id}"
        existing_entry = CustomerCreditLedger.objects.filter(
            account=account, idempotency_key=idem_key
        ).first()
        if existing_entry:
            return abs(existing_entry.amount_cents)

        available = services.available_credit_cents(account, contact)
        if available <= 0:
            return 0

        # Balance the credit can reduce: total minus manual discount minus friend
        # referral discount (both already reflected in revised_total, which does
        # not yet include wallet credit because referral_credit_amount is 0 here).
        balance_cents = dollars_to_cents(job.revised_total or 0)
        if balance_cents <= 0:
            return 0

        applied = min(available, balance_cents)
        if applied <= 0:
            return 0

        Job.objects.filter(pk=job.pk).update(
            referral_credit_amount=cents_to_dollars(applied)
        )
        job.referral_credit_amount = cents_to_dollars(applied)

        services.issue_credit(
            account=account,
            contact=contact,
            amount_cents=-applied,
            entry_type=CustomerCreditLedger.TYPE_APPLICATION,
            idempotency_key=idem_key,
            invoice_id=(job.invoice_id or ""),
            job_id=job.id,
            description=f"Referral credit applied on job {job.id}",
        )
        return applied
    except Exception as exc:
        logger.exception("Referral apply_credit_before_invoice_create failed: %s", exc)
        return 0


def finalize_credit_application_for_job(job, invoice_id: str) -> None:
    """Attach real GHL invoice id onto the job's credit application ledger row."""
    try:
        from referral_app.models import CustomerCreditLedger

        account = getattr(job, "account", None)
        if not account or not invoice_id:
            return
        CustomerCreditLedger.objects.filter(
            account=account,
            idempotency_key=f"application:job:{job.id}",
        ).update(invoice_id=invoice_id)
    except Exception as exc:
        logger.exception("Referral finalize_credit_application failed: %s", exc)
