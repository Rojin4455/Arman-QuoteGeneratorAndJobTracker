from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db import transaction
from decimal import Decimal

from .models import CustomService, QuoteSchedule, CustomerServiceSelection, CustomerPackageQuote
from service_app.models import GlobalBasePrice, User
from jobtracker_app.models import Job, JobServiceItem

@receiver([post_save, post_delete], sender=CustomService)
def update_submission_total(sender, instance, **kwargs):
    """Update the parent submission total whenever custom services change"""
    submission = instance.purchase
    submission.calculate_final_total()





def _resolve_user_from_reference(reference: str):
    if not reference:
        return None
    ref = reference.strip()
    if not ref:
        return None

    lookup_filters = [{"email__iexact": ref}, {"username__iexact": ref}]
    for filters in lookup_filters:
        try:
            return User.objects.filter(**filters).first()
        except Exception:
            continue
    return None


def _quantize_currency(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


@receiver(post_save, sender=QuoteSchedule)
def handle_quote_submission(sender, instance, created, **kwargs):
    """Create or update an internal job when a quote is submitted/scheduled."""

    if created or not instance.is_submitted:
        return

    submission = instance.submission
    contact = submission.contact
    address = submission.address

    customer_name = ""
    customer_email = None
    customer_phone = None
    ghl_contact_id = None

    if contact:
        customer_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
        customer_email = getattr(contact, "email", None)
        customer_phone = getattr(contact, "phone", None)
        ghl_contact_id = getattr(contact, "contact_id", None)

    customer_address = address.get_full_address() if address else None

    selected_services = CustomerServiceSelection.objects.filter(
        submission=submission,
        selected_package__isnull=False
    ).select_related("service", "selected_package")

    job_items = []
    total_price = Decimal("0.00")
    total_duration = Decimal("0.00")
    default_item_duration = Decimal("0.50")  # 30 minutes as hours

    for service_selection in selected_services:
        selected_quote = CustomerPackageQuote.objects.filter(
            service_selection=service_selection,
            is_selected=True
        ).order_by("-created_at").first()

        if not selected_quote and service_selection.selected_package:
            selected_quote = CustomerPackageQuote.objects.filter(
                service_selection=service_selection,
                package=service_selection.selected_package
            ).order_by("-created_at").first()

        if not selected_quote:
            continue

        price = _quantize_currency(Decimal(selected_quote.total_price))
        job_items.append(
            {
                "service": service_selection.service,
                "custom_name": None,
                "price": price,
                "duration_hours": default_item_duration,
            }
        )
        total_price += price
        total_duration += default_item_duration

    custom_services = CustomService.objects.filter(purchase=submission, is_active=True)
    for custom_service in custom_services:
        price = _quantize_currency(Decimal(custom_service.price))
        job_items.append(
            {
                "service": None,
                "custom_name": custom_service.product_name,
                "price": price,
                "duration_hours": default_item_duration,
            }
        )
        total_price += price
        total_duration += default_item_duration

    global_price = GlobalBasePrice.objects.first()
    if global_price:
        try:
            minimum_total = _quantize_currency(Decimal(global_price.base_price))
            if total_price < minimum_total:
                adjustment_amount = minimum_total - total_price
                if adjustment_amount > Decimal("0.00"):
                    job_items.append(
                        {
                            "service": None,
                            "custom_name": "Adjustments",
                            "price": _quantize_currency(adjustment_amount),
                            "duration_hours": Decimal("0.00"),
                        }
                    )
                    total_price = minimum_total
        except Exception:
            # Ignore errors converting global price so we still create the job
            pass

    total_price = _quantize_currency(total_price)
    total_duration = total_duration.quantize(Decimal("0.01"))

    # Get quoted_by user from submission model (ForeignKey)
    quoted_by_user = submission.quoted_by
    created_by_email = None
    if quoted_by_user:
        created_by_email = getattr(quoted_by_user, "email", None)
    else:
        # Fallback: try to resolve from QuoteSchedule's quoted_by string field for backward compatibility
        quoted_by_user = _resolve_user_from_reference(instance.quoted_by)
    if quoted_by_user:
        created_by_email = getattr(quoted_by_user, "email", None)
    elif instance.quoted_by and "@" in instance.quoted_by:
        created_by_email = instance.quoted_by

    job_defaults = {
        "title": customer_name or "Accepted Quote",
        "description": "Quote accepted and converted to job.",
        "priority": "medium",
        "duration_hours": total_duration,
        "scheduled_at": instance.scheduled_date,
        "total_price": total_price,
        "customer_name": customer_name or None,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "customer_address": customer_address,
        "ghl_contact_id": ghl_contact_id,
        "notes": instance.notes,
        "created_by_email": created_by_email,
    }

    with transaction.atomic():
        # Check if a job already exists for this submission with status 'to_convert'
        # If it does, update it; otherwise create a new one
        # Note: With ForeignKey, multiple jobs can exist per submission (e.g., recurring jobs)
        existing_job = Job.objects.filter(
            submission=submission,
            status='to_convert'
        ).first()
        
        if existing_job:
            # Update existing job
            job = existing_job
            for attr, value in job_defaults.items():
                setattr(job, attr, value)
            if quoted_by_user:
                job.quoted_by = quoted_by_user
            if not job.status:
                job.status = "to_convert"
            # Sync account from submission when job has no account
            if getattr(submission, 'account_id', None) and not job.account_id:
                job.account_id = submission.account_id
            job.save()
            job.items.all().delete()
        else:
            # Create new job (set account from submission for multi-account)
            job = Job.objects.create(
                submission=submission,
                **job_defaults,
                status="to_convert",
                account=getattr(submission, 'account', None),
                **({"quoted_by": quoted_by_user} if quoted_by_user else {}),
            )

        items_to_create = [
            JobServiceItem(
                job=job,
                service=item["service"],
                custom_name=item["custom_name"],
                price=item["price"],
                duration_hours=item["duration_hours"],
            )
            for item in job_items
        ]
        if items_to_create:
            JobServiceItem.objects.bulk_create(items_to_create)

        if submission.status != "accepted":
            submission.status = "accepted"
            submission.save(update_fields=["status"])
    