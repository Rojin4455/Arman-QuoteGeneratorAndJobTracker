"""
Clone services between GHL accounts (structure only by default) or duplicate within one account (with pricing).

Reads only from the source account; creates new rows on the destination account.
Does not copy: QuestionPricing, SubQuestionPricing, OptionPricing, ServicePackageSizeMapping.
Service.price and Package.base_price are set to zero on the copies.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from accounts.models import GHLAuthCredentials
from service_app.models import (
    Feature,
    OptionPricing,
    Package,
    PackageFeature,
    Question,
    QuestionOption,
    QuestionPricing,
    Service,
    ServicePackageSizeMapping,
    ServiceSettings,
    SubQuestion,
    SubQuestionPricing,
)


def copy_service_tree(
    old_service: Service,
    dest_account: GHLAuthCredentials,
    *,
    new_name: str | None = None,
    include_pricing: bool = False,
) -> Service:
    """
    Clone one service and related structure onto ``dest_account``.

    With ``include_pricing=True`` also copies Service.price, Package.base_price,
    question/sub-question/option pricing rules and (same account only) size mappings,
    so the copy calculates identically to the original.
    """
    feat_map: dict = {}
    pkg_map: dict = {}
    q_map: dict = {}
    opt_map: dict = {}
    sq_map: dict = {}

    new_svc = Service.objects.create(
        account=dest_account,
        name=new_name or old_service.name,
        description=old_service.description,
        price=old_service.price if include_pricing else Decimal("0.00"),
        hours=old_service.hours,
        is_active=old_service.is_active,
        order=old_service.order,
        created_by=None,
    )

    try:
        os_ = old_service.settings
    except ServiceSettings.DoesNotExist:
        pass
    else:
        ServiceSettings.objects.create(
            service=new_svc,
            general_disclaimer=os_.general_disclaimer,
            bid_in_person_disclaimer=os_.bid_in_person_disclaimer,
            apply_area_minimum=os_.apply_area_minimum,
            apply_house_size_minimum=os_.apply_house_size_minimum,
            apply_trip_charge_to_bid=os_.apply_trip_charge_to_bid,
            enable_dollar_minimum=os_.enable_dollar_minimum,
        )

    for f in old_service.features.all().order_by("order", "name"):
        feat_map[f.pk] = Feature.objects.create(
            service=new_svc,
            name=f.name,
            description=f.description,
            is_active=f.is_active,
            order=f.order,
        )

    for p in old_service.packages.all().order_by("order", "name"):
        pkg_map[p.pk] = Package.objects.create(
            service=new_svc,
            name=p.name,
            base_price=p.base_price if include_pricing else Decimal("0.00"),
            order=p.order,
            is_active=p.is_active,
        )

    for pf in PackageFeature.objects.filter(package__service=old_service).select_related(
        "package", "feature"
    ):
        PackageFeature.objects.create(
            package=pkg_map[pf.package_id],
            feature=feat_map[pf.feature_id],
            is_included=pf.is_included,
            order=pf.order,
        )

    old_questions = list(old_service.questions.all().order_by("order", "created_at"))
    remaining = old_questions[:]
    safety = 0
    while remaining:
        safety += 1
        if safety > len(old_questions) + 10:
            raise RuntimeError(
                f"Question dependency loop or missing refs for service {old_service.name!r}; "
                f"remaining={[str(q.pk) for q in remaining]}"
            )
        still = []
        for oq in remaining:
            if oq.parent_question_id and oq.parent_question_id not in q_map:
                still.append(oq)
                continue
            if oq.condition_option_id and oq.condition_option_id not in opt_map:
                still.append(oq)
                continue

            parent = q_map[oq.parent_question_id] if oq.parent_question_id else None
            cond_opt = opt_map[oq.condition_option_id] if oq.condition_option_id else None

            nq = Question.objects.create(
                service=new_svc,
                parent_question=parent,
                condition_answer=oq.condition_answer,
                condition_option=cond_opt,
                question_text=oq.question_text,
                question_type=oq.question_type,
                order=oq.order,
                is_active=oq.is_active,
            )
            q_map[oq.pk] = nq

            for oo in oq.options.all().order_by("order", "option_text"):
                opt_map[oo.pk] = QuestionOption.objects.create(
                    question=nq,
                    option_text=oo.option_text,
                    order=oo.order,
                    is_active=oo.is_active,
                    allow_quantity=oo.allow_quantity,
                    max_quantity=oo.max_quantity,
                )

        if len(still) == len(remaining):
            raise RuntimeError(
                f"Could not copy questions for {old_service.name!r}; "
                f"remaining={[str(q.pk) for q in still]}"
            )
        remaining = still

    for oq in old_questions:
        nq = q_map[oq.pk]
        for sq in oq.sub_questions.all().order_by("order", "sub_question_text"):
            sq_map[sq.pk] = SubQuestion.objects.create(
                parent_question=nq,
                sub_question_text=sq.sub_question_text,
                order=sq.order,
                is_active=sq.is_active,
            )

    if include_pricing:
        _copy_pricing(old_service, dest_account, pkg_map, q_map, opt_map, sq_map)

    return new_svc


def _copy_pricing(old_service, dest_account, pkg_map, q_map, opt_map, sq_map) -> None:
    for r in QuestionPricing.objects.filter(question__service=old_service):
        QuestionPricing.objects.create(
            question=q_map[r.question_id],
            package=pkg_map[r.package_id],
            yes_pricing_type=r.yes_pricing_type,
            yes_value=r.yes_value,
        )
    for r in SubQuestionPricing.objects.filter(sub_question__parent_question__service=old_service):
        SubQuestionPricing.objects.create(
            sub_question=sq_map[r.sub_question_id],
            package=pkg_map[r.package_id],
            yes_pricing_type=r.yes_pricing_type,
            yes_value=r.yes_value,
        )
    for r in OptionPricing.objects.filter(option__question__service=old_service):
        OptionPricing.objects.create(
            option=opt_map[r.option_id],
            package=pkg_map[r.package_id],
            pricing_type=r.pricing_type,
            value=r.value,
        )
    # Size ranges (GlobalSizePackage) are per account, so only reusable within the same account.
    if old_service.account_id == getattr(dest_account, "pk", None):
        for m in ServicePackageSizeMapping.objects.filter(service_package__service=old_service):
            ServicePackageSizeMapping.objects.create(
                service_package=pkg_map[m.service_package_id],
                global_size=m.global_size,
                price=m.price,
            )


def duplicate_service(service: Service, new_name: str) -> Service:
    """Duplicate a service inside its own account, including all pricing."""
    with transaction.atomic():
        return copy_service_tree(
            service, service.account, new_name=new_name, include_pricing=True
        )


def copy_all_services_between_locations(
    source_location_id: str,
    dest_location_id: str,
    *,
    use_transaction: bool = True,
) -> list[Service]:
    """
    Copy every service from the account identified by ``source_location_id`` to
    the account for ``dest_location_id``.

    Only creates rows on the destination; the source account is not modified.
    """
    source = GHLAuthCredentials.objects.get(location_id=source_location_id)
    dest = GHLAuthCredentials.objects.get(location_id=dest_location_id)
    old_services = list(Service.objects.filter(account=source).order_by("order", "name"))

    def _run() -> list[Service]:
        created: list[Service] = []
        for s in old_services:
            created.append(copy_service_tree(s, dest))
        return created

    if use_transaction:
        with transaction.atomic():
            return _run()
    return _run()
