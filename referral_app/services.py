"""
Customer referral domain logic for JobTracker.

JobTracker is source of truth for attribution, qualification, credits, and
invoice application. GHL is used for contacts/tags only.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import datetime
from typing import Optional, Tuple

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Contact, GHLAuthCredentials
from referral_app.money import assert_cents, cents_to_dollars, dollars_to_cents
from referral_app.models import (
    CustomerCreditLedger,
    ReferralAttribution,
    ReferralLink,
    ReferralProcessedEvent,
    ReferralProgram,
)

logger = logging.getLogger(__name__)

REFERRAL_CLAIMED_TAG = "referral claimed"
REFERRAL_PENDING_TAG = "referral pending"
REFERRER_TAG = "referrer"


def normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def clean_name(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())[:80]


def split_name(full_name: str) -> Tuple[str, str]:
    parts = clean_name(full_name).split(" ", 1)
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    return first, last


def contact_display_name(contact: Contact) -> str:
    name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    return name or (contact.email or "Customer")


def get_or_create_program(account: GHLAuthCredentials) -> ReferralProgram:
    program, _ = ReferralProgram.objects.get_or_create(account=account)
    return program


def make_referral_code(name: str) -> str:
    prefix = re.sub(r"[^a-z0-9]", "", (name or "").lower())[:7].upper() or "FRIEND"
    suffix = secrets.token_hex(3).upper()[:5]
    return f"{prefix}{suffix}"


def ensure_referral_link(account: GHLAuthCredentials, contact: Contact) -> ReferralLink:
    existing = ReferralLink.objects.filter(account=account, contact=contact).first()
    if existing:
        return existing

    display = contact_display_name(contact)
    for _ in range(8):
        code = make_referral_code(display)
        try:
            return ReferralLink.objects.create(
                account=account,
                contact=contact,
                code=code,
            )
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a unique referral code.")


def available_credit_cents(account: GHLAuthCredentials, contact: Contact) -> int:
    total = (
        CustomerCreditLedger.objects.filter(account=account, contact=contact)
        .aggregate(total=Sum("amount_cents"))
        .get("total")
    )
    return int(total or 0)


def lifetime_credit_cents(account: GHLAuthCredentials, contact: Contact) -> int:
    total = (
        CustomerCreditLedger.objects.filter(
            account=account,
            contact=contact,
            amount_cents__gt=0,
        )
        .aggregate(total=Sum("amount_cents"))
        .get("total")
    )
    return int(total or 0)


def monthly_referrer_issued_cents(account: GHLAuthCredentials, contact: Contact) -> int:
    now = timezone.now()
    month_start = timezone.make_aware(datetime(now.year, now.month, 1)) if timezone.is_naive(now) else now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    total = (
        CustomerCreditLedger.objects.filter(
            account=account,
            contact=contact,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            created_at__gte=month_start,
        )
        .aggregate(total=Sum("amount_cents"))
        .get("total")
    )
    return int(total or 0)


def calculate_rewards(program: ReferralProgram, monthly_issued_cents: int) -> dict:
    assert_cents(program.referrer_reward_cents, "Referrer reward")
    assert_cents(program.friend_reward_cents, "Friend reward")
    assert_cents(program.monthly_referrer_cap_cents, "Monthly referrer cap")
    assert_cents(monthly_issued_cents, "Monthly issued")

    cap_remaining = max(0, program.monthly_referrer_cap_cents - monthly_issued_cents)
    referrer_reward = (
        0
        if program.reward_mode == ReferralProgram.REWARD_FRIEND_ONLY
        else min(program.referrer_reward_cents, cap_remaining)
    )
    friend_reward = (
        0
        if program.reward_mode == ReferralProgram.REWARD_REFERRER_ONLY
        else program.friend_reward_cents
    )
    return {
        "referrer_reward_cents": referrer_reward,
        "friend_reward_cents": friend_reward,
    }


def build_share_url(code: str) -> str:
    frontend = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{frontend}/r/{code}"


def build_customer_hub_url(code: str, location_id: Optional[str] = None) -> str:
    frontend = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    url = f"{frontend}/refer?code={code}"
    if location_id:
        url = f"{url}&location_id={location_id}"
    return url


def _append_ghl_tags(contact: Contact, credentials: GHLAuthCredentials, tags_to_add: list[str]) -> None:
    """Best-effort GHL tag update; financial state does not depend on this."""
    if not tags_to_add:
        return
    try:
        import requests

        ghl_id = (contact.contact_id or "").strip()
        if not ghl_id or ghl_id.startswith("public_"):
            return

        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "Version": "2021-07-28",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        get_url = f"https://services.leadconnectorhq.com/contacts/{ghl_id}"
        resp = requests.get(get_url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return
        existing = list((resp.json().get("contact") or resp.json()).get("tags") or [])
        lower = {str(t).lower() for t in existing if isinstance(t, str)}
        merged = list(existing)
        for tag in tags_to_add:
            if tag.lower() not in lower:
                merged.append(tag)
        if merged == existing:
            return
        requests.put(
            get_url,
            headers=headers,
            json={"tags": merged},
            timeout=20,
        )
        contact.tags = merged
        contact.save(update_fields=["tags"])
    except Exception as exc:
        logger.warning("Referral GHL tag update failed for contact %s: %s", contact.pk, exc)


def _create_ghl_and_local_contact(
    account: GHLAuthCredentials,
    *,
    name: str,
    email: str,
    phone: Optional[str] = None,
    tags: Optional[list] = None,
) -> Contact:
    from quote_app.helpers import (
        create_or_update_ghl_contact_for_public,
        upsert_local_contact_from_public,
    )

    first, last = split_name(name)
    location_id = (account.location_id or "").strip()
    ghl_id = None
    if location_id and account.access_token:
        ghl_id = create_or_update_ghl_contact_for_public(
            account,
            location_id,
            first_name=first,
            last_name=last,
            email=email,
            phone=phone or "",
        )
    contact, _created = upsert_local_contact_from_public(
        account=account,
        location_id=location_id,
        ghl_contact_id=ghl_id,
        first_name=first,
        last_name=last,
        email=email,
        phone=phone or "",
    )
    if tags:
        existing = list(contact.tags or [])
        lower = {str(t).lower() for t in existing if isinstance(t, str)}
        for tag in tags:
            if tag.lower() not in lower:
                existing.append(tag)
        contact.tags = existing
        contact.save(update_fields=["tags"])
        _append_ghl_tags(contact, account, tags)
    return contact


def normalize_phone(value: Optional[str]) -> str:
    """Digits-only comparison form; keeps last 10 digits to survive +1 formatting."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _find_existing_contact(account: GHLAuthCredentials, email_norm: str, phone_norm: str) -> Optional[Contact]:
    """Duplicate check by email OR phone (not only one field)."""
    if email_norm:
        by_email = (
            Contact.objects.filter(account=account, email__iexact=email_norm)
            .order_by("-date_added", "-id")
            .first()
        )
        if by_email:
            return by_email
    if phone_norm:
        candidates = Contact.objects.filter(account=account, phone__isnull=False).exclude(phone="")
        # Match on normalized digits to survive formatting differences.
        for candidate in candidates.only("id", "phone").iterator():
            if normalize_phone(candidate.phone) == phone_norm:
                return Contact.objects.get(pk=candidate.pk)
    return None


@transaction.atomic
def claim_referral(
    *,
    code: str,
    name: str,
    email: str,
    phone: Optional[str] = None,
) -> dict:
    code_norm = (code or "").strip().upper()
    name_clean = clean_name(name)
    email_norm = normalize_email(email)
    phone_clean = (phone or "").strip()[:30] or None
    phone_norm = normalize_phone(phone_clean)

    if len(name_clean) < 2:
        raise ValueError("Please enter your name.")
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email_norm) or len(email_norm) > 200:
        raise ValueError("Please enter a valid email address.")

    link = (
        ReferralLink.objects.select_related("account", "contact")
        .filter(code__iexact=code_norm)
        .first()
    )
    if not link:
        raise ValueError("This referral link is not valid.")

    account = link.account
    program = get_or_create_program(account)
    if not program.enabled:
        raise ValueError("This referral program is not currently accepting referrals.")

    referrer = link.contact
    if normalize_email(referrer.email) == email_norm:
        raise ValueError("You cannot use your own referral link.")
    if phone_norm and normalize_phone(referrer.phone) == phone_norm:
        raise ValueError("You cannot use your own referral link.")

    existing = _find_existing_contact(account, email_norm, phone_norm)
    if existing:
        prior = ReferralAttribution.objects.filter(
            account=account, referred_contact=existing
        ).first()
        if prior and prior.referrer_contact_id == referrer.pk and prior.status in (
            ReferralAttribution.STATUS_PENDING,
            ReferralAttribution.STATUS_QUALIFIED,
        ):
            # Same person re-submitting the same referral — idempotent success.
            return {
                "referral_id": str(prior.id),
                "business_name": account.company_name or "our team",
                "friend_reward_cents": prior.friend_discount_cents,
                "referrer_reward_cents": program.referrer_reward_cents,
                "already_claimed": True,
            }
        # Existing customer for this business — not eligible as a new referral,
        # regardless of which referral link they used.
        raise ValueError(
            "You are already a customer of this business, so this referral offer "
            "is not available. Ask us about your own referral link instead!"
        )

    referred = _create_ghl_and_local_contact(
        account,
        name=name_clean,
        email=email_norm,
        phone=phone_clean,
        tags=[REFERRAL_CLAIMED_TAG, REFERRAL_PENDING_TAG],
    )
    if referred.pk == referrer.pk:
        raise ValueError("You cannot use your own referral link.")

    try:
        attribution = ReferralAttribution.objects.create(
            account=account,
            referrer_contact=referrer,
            referred_contact=referred,
            referral_code=link.code,
            referred_email=email_norm,
            referred_phone=phone_clean or "",
            status=ReferralAttribution.STATUS_PENDING,
            source="share_link",
            friend_discount_cents=(
                program.friend_reward_cents
                if program.reward_mode != ReferralProgram.REWARD_REFERRER_ONLY
                else 0
            ),
        )
    except IntegrityError:
        # Race: same person double-submitted the form. One attribution already exists.
        raise ValueError("This referral has already been claimed for this customer.")

    _append_ghl_tags(referrer, account, [REFERRER_TAG])
    ensure_referral_link(account, referred)

    return {
        "referral_id": str(attribution.id),
        "business_name": account.company_name or "our team",
        "friend_reward_cents": attribution.friend_discount_cents,
        "referrer_reward_cents": program.referrer_reward_cents,
    }


def get_claim_page_payload(code: str) -> Optional[dict]:
    link = (
        ReferralLink.objects.select_related("account", "contact")
        .filter(code__iexact=(code or "").strip())
        .first()
    )
    if not link:
        return None
    account = link.account
    program = get_or_create_program(account)
    if not program.enabled:
        return None
    return {
        "referral_code": link.code,
        "referrer_name": contact_display_name(link.contact),
        "business_name": account.company_name or "Business",
        "short_name": (account.company_name or "Business").split()[0],
        "service_label": program.service_label,
        "primary_color": program.primary_color,
        "accent_color": program.accent_color,
        "logo_url": account.company_logo_url or "",
        "location_id": account.location_id or "",
        "friend_reward_cents": program.friend_reward_cents,
        "referrer_reward_cents": program.referrer_reward_cents,
        "minimum_invoice_cents": program.minimum_invoice_cents,
        "terms_text": program.terms_text,
        "reward_mode": program.reward_mode,
        "website_url": "",
    }


def get_customer_hub(code: str) -> Optional[dict]:
    link = (
        ReferralLink.objects.select_related("account", "contact")
        .filter(code__iexact=(code or "").strip())
        .first()
    )
    if not link:
        return None
    account = link.account
    program = get_or_create_program(account)
    contact = link.contact
    referrals = (
        ReferralAttribution.objects.filter(account=account, referrer_contact=contact)
        .select_related("referred_contact")
        .order_by("-created_at")[:50]
    )
    return {
        "name": contact_display_name(contact),
        "referral_code": link.code,
        "share_url": build_share_url(link.code),
        "location_id": account.location_id or "",
        "short_name": (account.company_name or "Business").split()[0],
        "business_name": account.company_name or "Business",
        "primary_color": program.primary_color,
        "accent_color": program.accent_color,
        "logo_url": account.company_logo_url or "",
        "service_label": program.service_label,
        "available_credit_cents": available_credit_cents(account, contact),
        "lifetime_credit_cents": lifetime_credit_cents(account, contact),
        "reward_cents": program.referrer_reward_cents,
        "friend_reward_cents": program.friend_reward_cents,
        "minimum_invoice_cents": program.minimum_invoice_cents,
        "referrals": [
            {
                "friend_name": contact_display_name(r.referred_contact),
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in referrals
        ],
    }


@transaction.atomic
def issue_credit(
    *,
    account: GHLAuthCredentials,
    contact: Contact,
    amount_cents: int,
    entry_type: str,
    idempotency_key: str,
    referral: Optional[ReferralAttribution] = None,
    invoice_id: str = "",
    job_id=None,
    description: str = "",
) -> Optional[CustomerCreditLedger]:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise ValueError("Credit amount must be an integer number of cents.")
    if amount_cents == 0:
        return None
    if CustomerCreditLedger.objects.filter(account=account, idempotency_key=idempotency_key).exists():
        return CustomerCreditLedger.objects.get(account=account, idempotency_key=idempotency_key)

    balance = available_credit_cents(account, contact) + amount_cents
    if balance < 0:
        raise ValueError("Customer credit balance cannot go negative.")

    return CustomerCreditLedger.objects.create(
        account=account,
        contact=contact,
        referral=referral,
        entry_type=entry_type,
        amount_cents=amount_cents,
        balance_after_cents=balance,
        invoice_id=invoice_id or "",
        job_id=job_id,
        idempotency_key=idempotency_key,
        description=description[:255],
    )


@transaction.atomic
def qualify_referral_on_invoice_paid(
    *,
    account: GHLAuthCredentials,
    contact: Contact,
    invoice_id: str,
    invoice_cents: int,
    job_id=None,
) -> dict:
    """
    Qualify the pending referral when the referred customer's invoice is FULLY paid.

    The friend already received their discount upfront on the job/invoice; here
    only the referrer's wallet is credited (capped by the monthly limit).
    """
    assert_cents(invoice_cents, "Invoice amount")
    program = get_or_create_program(account)
    if not program.enabled:
        return {"qualified": False, "reason": "program_disabled"}

    event_id = f"invoice.paid:{invoice_id}"
    if ReferralProcessedEvent.objects.filter(account=account, event_id=event_id).exists():
        return {"qualified": False, "reason": "duplicate_event"}

    attribution = (
        ReferralAttribution.objects.select_for_update()
        .filter(
            account=account,
            referred_contact=contact,
            status=ReferralAttribution.STATUS_PENDING,
        )
        .first()
    )
    if not attribution:
        ReferralProcessedEvent.objects.get_or_create(
            account=account,
            event_id=event_id,
            defaults={"event_type": "invoice.paid"},
        )
        return {"qualified": False, "reason": "no_pending_referral"}

    if invoice_cents < program.minimum_invoice_cents:
        return {"qualified": False, "reason": "below_minimum"}

    monthly = monthly_referrer_issued_cents(account, attribution.referrer_contact)
    rewards = calculate_rewards(program, monthly)
    referrer_reward = rewards["referrer_reward_cents"]

    # Record what friend discount was actually applied on the qualifying job.
    discount_applied = 0
    try:
        from jobtracker_app.models import Job

        job = Job.objects.filter(pk=job_id).first() if job_id else None
        if job and job.referral_attribution_id == attribution.id:
            discount_applied = dollars_to_cents(job.effective_referral_discount or 0)
    except Exception:
        discount_applied = 0

    attribution.status = ReferralAttribution.STATUS_QUALIFIED
    attribution.qualifying_invoice_id = invoice_id
    attribution.qualifying_invoice_cents = invoice_cents
    attribution.qualifying_job_id = job_id
    attribution.qualified_at = timezone.now()
    attribution.discount_applied_cents = discount_applied
    attribution.reward_credited_cents = referrer_reward
    attribution.reward_credited_at = timezone.now() if referrer_reward > 0 else None
    attribution.save(
        update_fields=[
            "status",
            "qualifying_invoice_id",
            "qualifying_invoice_cents",
            "qualifying_job_id",
            "qualified_at",
            "discount_applied_cents",
            "reward_credited_cents",
            "reward_credited_at",
            "updated_at",
        ]
    )

    if referrer_reward > 0:
        issue_credit(
            account=account,
            contact=attribution.referrer_contact,
            amount_cents=referrer_reward,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key=f"referrer_reward:{attribution.id}",
            referral=attribution,
            invoice_id=invoice_id,
            job_id=job_id,
            description=f"Referral reward for {contact_display_name(attribution.referred_contact)}",
        )

    ReferralProcessedEvent.objects.get_or_create(
        account=account,
        event_id=event_id,
        defaults={"event_type": "invoice.paid"},
    )
    return {
        "qualified": True,
        "referral_id": str(attribution.id),
        "referrer_reward_cents": referrer_reward,
        "friend_discount_applied_cents": discount_applied,
    }


def pending_attribution_for_contact(
    account: GHLAuthCredentials, contact: Contact
) -> Optional[ReferralAttribution]:
    return (
        ReferralAttribution.objects.filter(
            account=account,
            referred_contact=contact,
            status=ReferralAttribution.STATUS_PENDING,
        )
        .select_related("referrer_contact")
        .first()
    )


@transaction.atomic
def attach_referral_discount_to_job(job) -> dict:
    """
    Link the referred customer's pending referral onto their first job and set
    the friend discount amount. Idempotent; safe to call on every job create.
    """
    from referral_app.hooks import resolve_contact_for_job

    account = getattr(job, "account", None)
    contact = resolve_contact_for_job(job)
    if not account or not contact:
        return {"attached": False, "reason": "no_contact"}
    if job.referral_attribution_id:
        return {"attached": True, "reason": "already_attached"}

    program = get_or_create_program(account)
    if not program.enabled:
        return {"attached": False, "reason": "program_disabled"}

    attribution = (
        ReferralAttribution.objects.select_for_update()
        .filter(
            account=account,
            referred_contact=contact,
            status=ReferralAttribution.STATUS_PENDING,
        )
        .first()
    )
    if not attribution:
        return {"attached": False, "reason": "no_pending_referral"}
    if attribution.discount_job_id and attribution.discount_job_id != job.id:
        # Discount already carried by another active job of this customer.
        return {"attached": False, "reason": "discount_on_other_job"}

    discount_cents = 0 if attribution.discount_disabled else attribution.friend_discount_cents
    total_cents = dollars_to_cents(job.total_price or 0)
    discount_cents = min(discount_cents, total_cents) if total_cents > 0 else discount_cents

    attribution.discount_job_id = job.id
    attribution.save(update_fields=["discount_job_id", "updated_at"])

    from jobtracker_app.models import Job

    Job.objects.filter(pk=job.pk).update(
        referral_attribution=attribution,
        referral_discount_amount=cents_to_dollars(discount_cents),
        apply_referral_discount=not attribution.discount_disabled,
    )
    job.referral_attribution = attribution
    job.referral_discount_amount = cents_to_dollars(discount_cents)
    job.apply_referral_discount = not attribution.discount_disabled
    return {"attached": True, "discount_cents": discount_cents}


@transaction.atomic
def release_referral_discount_from_job(job) -> bool:
    """When a job is cancelled before payment, free the pending referral so the
    discount can attach to the customer's next job."""
    attribution_id = getattr(job, "referral_attribution_id", None)
    if not attribution_id:
        return False
    attribution = (
        ReferralAttribution.objects.select_for_update().filter(pk=attribution_id).first()
    )
    if not attribution or attribution.status != ReferralAttribution.STATUS_PENDING:
        return False
    if attribution.discount_job_id == job.id:
        attribution.discount_job_id = None
        attribution.save(update_fields=["discount_job_id", "updated_at"])

    from jobtracker_app.models import Job

    Job.objects.filter(pk=job.pk).update(
        referral_attribution=None,
        referral_discount_amount=cents_to_dollars(0),
    )
    return True


@transaction.atomic
def set_job_referral_discount(job, *, enabled: bool, changed_by: str = "") -> dict:
    """Admin override: enable/disable the referral discount on a job (tracked)."""
    attribution_id = getattr(job, "referral_attribution_id", None)
    if not attribution_id:
        return {"ok": False, "reason": "no_referral_on_job"}
    attribution = (
        ReferralAttribution.objects.select_for_update().filter(pk=attribution_id).first()
    )
    if not attribution:
        return {"ok": False, "reason": "attribution_missing"}

    from jobtracker_app.models import Job

    if enabled:
        discount_cents = attribution.friend_discount_cents
        total_cents = dollars_to_cents(job.total_price or 0)
        if total_cents > 0:
            discount_cents = min(discount_cents, total_cents)
        attribution.discount_disabled = False
        attribution.discount_disabled_by = ""
        Job.objects.filter(pk=job.pk).update(
            apply_referral_discount=True,
            referral_discount_amount=cents_to_dollars(discount_cents),
        )
    else:
        attribution.discount_disabled = True
        attribution.discount_disabled_by = (changed_by or "")[:150]
        Job.objects.filter(pk=job.pk).update(apply_referral_discount=False)
    attribution.save(update_fields=["discount_disabled", "discount_disabled_by", "updated_at"])
    return {"ok": True, "enabled": enabled}


@transaction.atomic
def apply_credit_to_invoice(
    *,
    account: GHLAuthCredentials,
    contact: Contact,
    invoice_id: str,
    invoice_balance_cents: int,
    job_id=None,
    requested_cents: Optional[int] = None,
) -> dict:
    """Auto-apply available referral credit, capped by invoice balance."""
    assert_cents(invoice_balance_cents, "Invoice balance")
    if requested_cents is not None:
        assert_cents(requested_cents, "Requested credit")

    available = available_credit_cents(account, contact)
    if available <= 0 or invoice_balance_cents <= 0:
        return {"applied_cents": 0, "remaining_credit_cents": available}

    requested = requested_cents if requested_cents is not None else available
    applied = min(requested, available, invoice_balance_cents)
    if applied <= 0:
        return {"applied_cents": 0, "remaining_credit_cents": available}

    idempotency_key = f"application:{invoice_id}:{contact.pk}"
    entry = issue_credit(
        account=account,
        contact=contact,
        amount_cents=-applied,
        entry_type=CustomerCreditLedger.TYPE_APPLICATION,
        idempotency_key=idempotency_key,
        invoice_id=invoice_id,
        job_id=job_id,
        description=f"Applied to invoice {invoice_id}",
    )
    return {
        "applied_cents": applied if entry else 0,
        "remaining_credit_cents": available_credit_cents(account, contact),
        "dollars": float(cents_to_dollars(applied)),
    }


@transaction.atomic
def reverse_invoice_credit_application(
    *,
    account: GHLAuthCredentials,
    invoice_id: str,
    reason: str = "voided",
) -> dict:
    apps = list(
        CustomerCreditLedger.objects.select_for_update().filter(
            account=account,
            invoice_id=invoice_id,
            entry_type=CustomerCreditLedger.TYPE_APPLICATION,
        )
    )
    reversed_total = 0
    for app in apps:
        key = f"reversal:{reason}:{app.idempotency_key}"
        if CustomerCreditLedger.objects.filter(account=account, idempotency_key=key).exists():
            continue
        restore = abs(app.amount_cents)
        issue_credit(
            account=account,
            contact=app.contact,
            amount_cents=restore,
            entry_type=CustomerCreditLedger.TYPE_REVERSAL,
            idempotency_key=key,
            referral=app.referral,
            invoice_id=invoice_id,
            job_id=app.job_id,
            description=f"Reversal ({reason}) for invoice {invoice_id}",
        )
        reversed_total += restore

    # If the qualifying invoice itself is voided/refunded, reverse rewards too.
    attribution = (
        ReferralAttribution.objects.select_for_update()
        .filter(
            account=account,
            qualifying_invoice_id=invoice_id,
            status=ReferralAttribution.STATUS_QUALIFIED,
        )
        .first()
    )
    if attribution:
        reward_entries = CustomerCreditLedger.objects.filter(
            account=account,
            referral=attribution,
            entry_type__in=[
                CustomerCreditLedger.TYPE_REFERRER_REWARD,
                CustomerCreditLedger.TYPE_FRIEND_REWARD,
            ],
        )
        for entry in reward_entries:
            key = f"reward_reversal:{reason}:{entry.idempotency_key}"
            if CustomerCreditLedger.objects.filter(account=account, idempotency_key=key).exists():
                continue
            issue_credit(
                account=account,
                contact=entry.contact,
                amount_cents=-entry.amount_cents,
                entry_type=CustomerCreditLedger.TYPE_REVERSAL,
                idempotency_key=key,
                referral=attribution,
                invoice_id=invoice_id,
                description=f"Referral reward reversal ({reason})",
            )
        attribution.status = ReferralAttribution.STATUS_REVERSED
        attribution.reversed_at = timezone.now()
        attribution.save(update_fields=["status", "reversed_at", "updated_at"])

    return {"reversed_cents": reversed_total}


def handle_job_completed_invitation(job) -> dict:
    """
    On job complete: add GHL tag 'referral invite' for that customer.

    Existing customers are included even if they never generated a referral
    link. We create a link if missing (for the GHL Referral Link field), but
    the tag is not gated on that.
    """
    account = getattr(job, "account", None)
    if not account:
        return {"skipped": "no_account"}
    program = get_or_create_program(account)
    if not program.enabled or not program.auto_invite_enabled:
        return {"skipped": "disabled"}
    if program.invitation_trigger not in (
        ReferralProgram.INVITE_COMPLETED_JOB,
        ReferralProgram.INVITE_EITHER,
    ):
        return {"skipped": "trigger_mismatch"}

    contact = getattr(job, "contact", None)
    if not contact and getattr(job, "ghl_contact_id", None):
        contact = Contact.objects.filter(contact_id=job.ghl_contact_id, account=account).first()
    if not contact and getattr(job, "customer_email", None):
        contact = (
            Contact.objects.filter(account=account, email__iexact=normalize_email(job.customer_email))
            .order_by("-id")
            .first()
        )
    if not contact:
        return {"skipped": "no_contact"}

    event_id = f"job.completed:{job.id}"
    if ReferralProcessedEvent.objects.filter(account=account, event_id=event_id).exists():
        return {"skipped": "duplicate"}

    share_url = ""
    link = None
    try:
        link = ensure_referral_link(account, contact)
        share_url = build_share_url(link.code)
    except Exception:
        logger.exception(
            "Referral invite: could not create link for contact %s job %s; tagging anyway",
            contact.pk,
            job.id,
        )

    from referral_app.ghl_sync import push_referral_link_and_invite_tag

    pushed = push_referral_link_and_invite_tag(contact, account, share_url)
    if not pushed:
        logger.warning(
            "Referral invite: GHL tag not added for contact %s job %s",
            contact.pk,
            job.id,
        )
    ReferralProcessedEvent.objects.get_or_create(
        account=account,
        event_id=event_id,
        defaults={"event_type": "job.completed"},
    )
    if not link:
        return {"tagged": pushed, "referral_code": None}
    return {
        "tagged": pushed,
        "referral_code": link.code,
        "share_url": share_url,
        "hub_url": build_customer_hub_url(link.code, account.location_id),
    }


def attach_pending_referral_to_quote(account: GHLAuthCredentials, contact: Contact, quote_id) -> bool:
    attribution = ReferralAttribution.objects.filter(
        account=account,
        referred_contact=contact,
        status=ReferralAttribution.STATUS_PENDING,
    ).first()
    if not attribution:
        return False
    if attribution.quote_id:
        return False
    attribution.quote_id = quote_id if isinstance(quote_id, uuid.UUID) else uuid.UUID(str(quote_id))
    attribution.save(update_fields=["quote_id", "updated_at"])
    return True


def plan_credit_for_job_invoice(job) -> dict:
    """
    Before creating a GHL invoice for a job, compute referral credit to apply
    as a fixed discount (dollars). Returns discount fields for Job + applied cents.
    """
    account = getattr(job, "account", None)
    contact = getattr(job, "contact", None)
    if not account or not contact:
        return {"applied_cents": 0, "discount_dollars": Decimal0()}

    program = get_or_create_program(account)
    if not program.enabled:
        return {"applied_cents": 0, "discount_dollars": Decimal0()}

    balance_cents = dollars_to_cents(getattr(job, "revised_total", None) or job.total_price or 0)
    if balance_cents <= 0:
        return {"applied_cents": 0, "discount_dollars": Decimal0()}

    available = available_credit_cents(account, contact)
    applied = min(available, balance_cents)
    return {
        "applied_cents": applied,
        "discount_dollars": cents_to_dollars(applied),
        "available_cents": available,
    }


def Decimal0():
    from decimal import Decimal

    return Decimal("0.00")


def serialize_program(account: GHLAuthCredentials, program: ReferralProgram | None = None) -> dict:
    program = program or get_or_create_program(account)
    return {
        "enabled": program.enabled,
        "reward_mode": program.reward_mode,
        "referrer_reward_cents": program.referrer_reward_cents,
        "friend_reward_cents": program.friend_reward_cents,
        "minimum_invoice_cents": program.minimum_invoice_cents,
        "monthly_referrer_cap_cents": program.monthly_referrer_cap_cents,
        "invitation_trigger": program.invitation_trigger,
        "auto_invite_enabled": program.auto_invite_enabled,
        "email_invite_enabled": program.email_invite_enabled,
        "sms_invite_enabled": program.sms_invite_enabled,
        "email_delay_minutes": program.email_delay_minutes,
        "sms_delay_minutes": program.sms_delay_minutes,
        "email_cadence": program.email_cadence,
        "sms_cadence": program.sms_cadence,
        "primary_color": program.primary_color,
        "accent_color": program.accent_color,
        "service_label": program.service_label,
        "terms_text": program.terms_text,
        "business_name": account.company_name or "",
        "logo_url": account.company_logo_url or "",
        "location_id": account.location_id or "",
    }


def owner_dashboard(account: GHLAuthCredentials) -> dict:
    program = get_or_create_program(account)
    base_qs = ReferralAttribution.objects.filter(account=account)
    stats_row = base_qs.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=ReferralAttribution.STATUS_PENDING)),
        qualified=Count("id", filter=Q(status=ReferralAttribution.STATUS_QUALIFIED)),
        influenced=Sum(
            "qualifying_invoice_cents",
            filter=Q(status=ReferralAttribution.STATUS_QUALIFIED),
        ),
    )
    attributions = list(
        base_qs.select_related("referrer_contact", "referred_contact").order_by("-created_at")[:100]
    )
    issued = (
        CustomerCreditLedger.objects.filter(account=account, amount_cents__gt=0)
        .aggregate(total=Sum("amount_cents"))
        .get("total")
        or 0
    )
    available = sum(
        row["bal"] or 0
        for row in CustomerCreditLedger.objects.filter(account=account)
        .values("contact_id")
        .annotate(bal=Sum("amount_cents"))
        .filter(bal__gt=0)
    )

    links = list(
        ReferralLink.objects.filter(account=account)
        .select_related("contact")
        .order_by("-created_at")[:100]
    )
    link_contact_ids = [link.contact_id for link in links]
    credit_map = {}
    qualified_map = {}
    if link_contact_ids:
        credit_map = {
            row["contact_id"]: row
            for row in CustomerCreditLedger.objects.filter(
                account=account,
                contact_id__in=link_contact_ids,
            )
            .values("contact_id")
            .annotate(
                available=Sum("amount_cents"),
                lifetime=Sum("amount_cents", filter=Q(amount_cents__gt=0)),
            )
        }
        qualified_map = {
            row["referrer_contact_id"]: row["c"]
            for row in ReferralAttribution.objects.filter(
                account=account,
                referrer_contact_id__in=link_contact_ids,
                status=ReferralAttribution.STATUS_QUALIFIED,
            )
            .values("referrer_contact_id")
            .annotate(c=Count("id"))
        }

    customers = []
    for link in links:
        credit = credit_map.get(link.contact_id) or {}
        customers.append(
            {
                "id": link.contact_id,
                "name": contact_display_name(link.contact),
                "email": (link.contact.email if link.contact else "") or "",
                "referral_code": link.code,
                "share_url": build_share_url(link.code),
                "available_credit_cents": int(credit.get("available") or 0),
                "lifetime_credit_cents": int(credit.get("lifetime") or 0),
                "qualified_referrals": int(qualified_map.get(link.contact_id) or 0),
            }
        )

    ledger = (
        CustomerCreditLedger.objects.filter(account=account)
        .select_related("contact")
        .order_by("-created_at")[:50]
    )

    return {
        "program": serialize_program(account, program),
        "stats": {
            "total": int(stats_row.get("total") or 0),
            "pending": int(stats_row.get("pending") or 0),
            "qualified": int(stats_row.get("qualified") or 0),
            "credits_issued_cents": int(issued),
            "credits_available_cents": int(available),
            "influenced_revenue_cents": int(stats_row.get("influenced") or 0),
        },
        "referrals": [
            {
                "id": str(a.id),
                "referrer_name": contact_display_name(a.referrer_contact),
                "referred_name": contact_display_name(a.referred_contact),
                "referred_email": a.referred_email,
                "referral_code": a.referral_code,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
                "qualifying_invoice_id": a.qualifying_invoice_id,
                "qualifying_invoice_cents": a.qualifying_invoice_cents,
                "qualifying_job_id": str(a.qualifying_job_id) if a.qualifying_job_id else None,
                "friend_discount_cents": a.friend_discount_cents,
                "discount_applied_cents": a.discount_applied_cents,
                "discount_disabled": a.discount_disabled,
                "discount_disabled_by": a.discount_disabled_by,
                "reward_credited_cents": a.reward_credited_cents,
                "reward_credited_at": a.reward_credited_at.isoformat() if a.reward_credited_at else None,
            }
            for a in attributions
        ],
        "customers": customers,
        "ledger": [
            {
                "id": str(e.id),
                "customer_name": contact_display_name(e.contact),
                "entry_type": e.entry_type,
                "amount_cents": e.amount_cents,
                "balance_after_cents": e.balance_after_cents,
                "description": e.description,
                "created_at": e.created_at.isoformat(),
            }
            for e in ledger
        ],
    }


def update_program(account: GHLAuthCredentials, data: dict) -> ReferralProgram:
    program = get_or_create_program(account)
    allowed = {
        "enabled",
        "reward_mode",
        "referrer_reward_cents",
        "friend_reward_cents",
        "minimum_invoice_cents",
        "monthly_referrer_cap_cents",
        "invitation_trigger",
        "auto_invite_enabled",
        "email_invite_enabled",
        "sms_invite_enabled",
        "email_delay_minutes",
        "sms_delay_minutes",
        "email_cadence",
        "sms_cadence",
        "primary_color",
        "accent_color",
        "service_label",
        "terms_text",
    }
    for key, value in data.items():
        if key not in allowed:
            continue
        if key.endswith("_cents") or key.endswith("_minutes"):
            value = int(value)
            if value < 0 or value > 1_000_000:
                raise ValueError(f"{key} out of range")
        setattr(program, key, value)
    if program.reward_mode not in dict(ReferralProgram.REWARD_MODE_CHOICES):
        raise ValueError("Invalid reward mode.")
    if program.invitation_trigger not in dict(ReferralProgram.INVITATION_TRIGGER_CHOICES):
        raise ValueError("Invalid invitation trigger.")
    program.save()
    return program


def ensure_link_for_contact_id(account: GHLAuthCredentials, contact_id: int) -> dict:
    contact = Contact.objects.filter(account=account, pk=contact_id).first()
    if not contact:
        raise ValueError("Contact not found.")
    link = ensure_referral_link(account, contact)
    share_url = build_share_url(link.code)
    try:
        from referral_app.ghl_sync import push_referral_link_and_invite_tag

        push_referral_link_and_invite_tag(contact, account, share_url)
    except Exception as exc:
        logger.warning("GHL referral link push failed for contact %s: %s", contact.pk, exc)
    return {
        "referral_code": link.code,
        "share_url": share_url,
        "hub_url": build_customer_hub_url(link.code, account.location_id),
        "available_credit_cents": available_credit_cents(account, contact),
    }
