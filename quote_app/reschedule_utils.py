"""
Deep-clone a CustomerSubmission for job reschedule (new date/time, pending conversion).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import (
    CustomerSubmission,
    CustomerServiceSelection,
    CustomerPackageQuote,
    CustomerQuestionResponse,
    CustomerOptionResponse,
    CustomerSubQuestionResponse,
    CustomService,
    QuoteSchedule,
)
from .quote_schedule_job_sync import create_reschedule_pending_job


def clone_submission_for_reschedule(source: CustomerSubmission, *, scheduled_at, job, notes: str = "", quoted_by_str: str = ""):
    """
    Copy submission graph into a new submission (status accepted), QuoteSchedule (not submitted),
    and a new Job with status reschedule_pending.
    """
    from datetime import timedelta

    extra = dict(source.additional_data or {})
    extra["reschedule_created_at"] = timezone.now().isoformat()
    if job and job.pk:
        extra["reschedule_source_job_id"] = str(job.pk)

    with transaction.atomic():
        new_sub = CustomerSubmission.objects.create(
            account=source.account,
            contact=source.contact,
            address=source.address,
            house_sqft=source.house_sqft,
            location=source.location,
            status="accepted",
            quoted_by=source.quoted_by,
            total_base_price=source.total_base_price,
            total_adjustments=source.total_adjustments,
            total_surcharges=source.total_surcharges,
            quote_surcharge_applicable=source.quote_surcharge_applicable,
            custom_service_total=source.custom_service_total,
            final_total=source.final_total,
            additional_data=extra,
            expires_at=timezone.now() + timedelta(days=30),
        )

        sel_map: dict = {}

        for old_sel in CustomerServiceSelection.objects.filter(submission=source).select_related("service", "selected_package"):
            new_sel = CustomerServiceSelection.objects.create(
                submission=new_sub,
                service=old_sel.service,
                selected_package=old_sel.selected_package,
                question_adjustments=old_sel.question_adjustments,
                surcharge_applicable=old_sel.surcharge_applicable,
                surcharge_amount=old_sel.surcharge_amount,
                final_base_price=old_sel.final_base_price,
                final_sqft_price=old_sel.final_sqft_price,
                final_total_price=old_sel.final_total_price,
            )
            sel_map[old_sel.pk] = new_sel

        for old_pkg in CustomerPackageQuote.objects.filter(service_selection__submission=source).select_related(
            "service_selection", "package"
        ):
            new_sel = sel_map.get(old_pkg.service_selection_id)
            if not new_sel:
                continue
            CustomerPackageQuote.objects.create(
                service_selection=new_sel,
                package=old_pkg.package,
                base_price=old_pkg.base_price,
                sqft_price=old_pkg.sqft_price,
                question_adjustments=old_pkg.question_adjustments,
                surcharge_amount=old_pkg.surcharge_amount,
                total_price=old_pkg.total_price,
                included_features=list(old_pkg.included_features or []),
                excluded_features=list(old_pkg.excluded_features or []),
                is_selected=old_pkg.is_selected,
            )

        qr_map: dict = {}

        for old_qr in CustomerQuestionResponse.objects.filter(service_selection__submission=source).select_related(
            "service_selection", "question"
        ):
            new_sel = sel_map.get(old_qr.service_selection_id)
            if not new_sel:
                continue
            new_qr = CustomerQuestionResponse.objects.create(
                service_selection=new_sel,
                question=old_qr.question,
                yes_no_answer=old_qr.yes_no_answer,
                text_answer=old_qr.text_answer,
                price_adjustment=old_qr.price_adjustment,
            )
            qr_map[old_qr.pk] = new_qr

        for old_opt in CustomerOptionResponse.objects.filter(question_response__service_selection__submission=source).select_related(
            "question_response", "option"
        ):
            new_qr = qr_map.get(old_opt.question_response_id)
            if not new_qr:
                continue
            CustomerOptionResponse.objects.create(
                question_response=new_qr,
                option=old_opt.option,
                quantity=old_opt.quantity,
                price_adjustment=old_opt.price_adjustment,
            )

        for old_sq in CustomerSubQuestionResponse.objects.filter(question_response__service_selection__submission=source).select_related(
            "question_response", "sub_question"
        ):
            new_qr = qr_map.get(old_sq.question_response_id)
            if not new_qr:
                continue
            CustomerSubQuestionResponse.objects.create(
                question_response=new_qr,
                sub_question=old_sq.sub_question,
                answer=old_sq.answer,
                price_adjustment=old_sq.price_adjustment,
            )

        for old_cs in CustomService.objects.filter(purchase=source):
            CustomService.objects.create(
                purchase=new_sub,
                product_name=old_cs.product_name,
                description=old_cs.description,
                is_active=old_cs.is_active,
                price=old_cs.price,
            )

        qs_notes = notes or ""
        quote_schedule = QuoteSchedule.objects.create(
            submission=new_sub,
            first_time=False,
            quoted_by=quoted_by_str or "",
            scheduled_date=scheduled_at,
            is_submitted=True,
            notes=qs_notes,
        )

        new_job = create_reschedule_pending_job(new_sub, quote_schedule)

    return new_sub, new_job
