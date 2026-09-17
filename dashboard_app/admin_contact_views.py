"""
Admin contact hub: list contacts with aggregate counts and retrieve full related graph
(quotes/submissions, jobs, invoices, appointments, addresses).
"""
from django.core.paginator import InvalidPage
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django_filters import rest_framework as filters
from rest_framework import filters as drf_filters
from rest_framework.mixins import UpdateModelMixin
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from accounts.account_scope import get_account_from_request
from accounts.mixins import AccountScopedQuerysetMixin
from accounts.models import Address, Contact
from dashboard_app.admin_contact_serializers import (
    AdminContactDetailSerializer,
    AdminContactListSerializer,
    AdminContactTaxExemptUpdateSerializer,
)
from dashboard_app.models import Invoice
from jobtracker_app.models import Job, JobAssignment
from quote_app.models import CustomerSubmission
from service_app.models import Appointment


class AdminContactPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        """Clamp out-of-range pages instead of 404 Invalid page."""
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        page_number = self.get_page_number(request, paginator)
        try:
            self.page = paginator.page(page_number)
        except InvalidPage:
            self.page = paginator.page(paginator.num_pages or 1)

        if paginator.num_pages > 1 and self.template is not None:
            self.display_page_controls = True

        self.request = request
        return list(self.page)


# Jobs that are still in play (not finished or cancelled).
_NON_TERMINAL_JOB_STATUSES = (
    'to_convert',
    'pending',
    'confirmed',
    'service_due',
    'on_the_way',
    'in_progress',
    'onhold',
)


def _count_map(queryset, group_field):
    return {
        row[group_field]: row['c']
        for row in queryset.values(group_field).annotate(c=Count('id'))
        if row[group_field] is not None
    }


def attach_contact_list_counts(contacts):
    """
    Fill list-row count attributes for one page of contacts.

    Counts run as grouped queries on this page's IDs only. Annotating the full
    list queryset with JOIN Counts made COUNT/OFFSET scan explode and timed
    out page 2+ on production.
    """
    if not contacts:
        return contacts

    pks = [c.pk for c in contacts]
    ghl_ids = [c.contact_id for c in contacts if c.contact_id]
    account_ids = {c.account_id for c in contacts if c.account_id}

    job_rows = (
        Job.objects.filter(contact_id__in=pks)
        .values('contact_id')
        .annotate(
            total=Count('id'),
            pending=Count('id', filter=Q(status__in=_NON_TERMINAL_JOB_STATUSES)),
        )
    )
    job_map = {row['contact_id']: row for row in job_rows}

    sub_map = _count_map(
        CustomerSubmission.objects.filter(contact_id__in=pks),
        'contact_id',
    )
    addr_map = _count_map(
        Address.objects.filter(contact_id__in=pks),
        'contact_id',
    )
    appt_map = _count_map(
        Appointment.objects.filter(contact_id__in=ghl_ids),
        'contact_id',
    )
    inv_qs = Invoice.objects.filter(contact_id__in=ghl_ids)
    if account_ids:
        inv_qs = inv_qs.filter(account_id__in=account_ids)
    inv_map = _count_map(inv_qs, 'contact_id')

    for contact in contacts:
        jobs = job_map.get(contact.pk) or {}
        contact.jobs_count = int(jobs.get('total') or 0)
        contact.pending_jobs_count = int(jobs.get('pending') or 0)
        contact.submissions_count = int(sub_map.get(contact.pk) or 0)
        contact.addresses_count = int(addr_map.get(contact.pk) or 0)
        contact.appointments_count = int(appt_map.get(contact.contact_id) or 0)
        contact.invoices_count = int(inv_map.get(contact.contact_id) or 0)
    return contacts


def _bool_exists(queryset, exists_qs, value):
    if value:
        return queryset.filter(Exists(exists_qs))
    return queryset.filter(~Exists(exists_qs))


def _bool_present(queryset, field, value):
    empty = Q(**{f'{field}__isnull': True}) | Q(**{field: ''})
    return queryset.exclude(empty) if value else queryset.filter(empty)


class AdminContactFilter(filters.FilterSet):
    """Optional list filters. Omit a param to leave it unconstrained."""

    tax_exempt = filters.BooleanFilter(field_name='tax_exempt')
    dnd = filters.BooleanFilter(field_name='dnd')
    has_email = filters.BooleanFilter(method='filter_has_email')
    has_phone = filters.BooleanFilter(method='filter_has_phone')
    has_company = filters.BooleanFilter(method='filter_has_company')
    date_added_after = filters.DateFilter(field_name='date_added', lookup_expr='date__gte')
    date_added_before = filters.DateFilter(field_name='date_added', lookup_expr='date__lte')
    has_jobs = filters.BooleanFilter(method='filter_has_jobs')
    has_pending_jobs = filters.BooleanFilter(method='filter_has_pending_jobs')
    has_quotes = filters.BooleanFilter(method='filter_has_quotes')
    has_invoices = filters.BooleanFilter(method='filter_has_invoices')
    has_addresses = filters.BooleanFilter(method='filter_has_addresses')

    class Meta:
        model = Contact
        fields = [
            'tax_exempt',
            'dnd',
            'has_email',
            'has_phone',
            'has_company',
            'date_added_after',
            'date_added_before',
            'has_jobs',
            'has_pending_jobs',
            'has_quotes',
            'has_invoices',
            'has_addresses',
        ]

    def filter_has_email(self, queryset, name, value):
        return _bool_present(queryset, 'email', value)

    def filter_has_phone(self, queryset, name, value):
        return _bool_present(queryset, 'phone', value)

    def filter_has_company(self, queryset, name, value):
        return _bool_present(queryset, 'company_name', value)

    def filter_has_jobs(self, queryset, name, value):
        return _bool_exists(queryset, Job.objects.filter(contact_id=OuterRef('pk')), value)

    def filter_has_pending_jobs(self, queryset, name, value):
        return _bool_exists(
            queryset,
            Job.objects.filter(contact_id=OuterRef('pk'), status__in=_NON_TERMINAL_JOB_STATUSES),
            value,
        )

    def filter_has_quotes(self, queryset, name, value):
        return _bool_exists(
            queryset,
            CustomerSubmission.objects.filter(contact_id=OuterRef('pk')),
            value,
        )

    def filter_has_invoices(self, queryset, name, value):
        return _bool_exists(
            queryset,
            Invoice.objects.filter(contact_id=OuterRef('contact_id')),
            value,
        )

    def filter_has_addresses(self, queryset, name, value):
        return _bool_exists(queryset, Address.objects.filter(contact_id=OuterRef('pk')), value)


class AdminContactViewSet(AccountScopedQuerysetMixin, UpdateModelMixin, ReadOnlyModelViewSet):
    """
    List and retrieve GHL contacts scoped to an account (via auth user, location_id, or default).

    **List** ``GET /api/dashboard/contacts/``
    Optional query params: ``search``, ``location_id``, ``tax_exempt``, ``dnd``,
    ``has_email``, ``has_phone``, ``has_company``, ``date_added_after``, ``date_added_before``,
    ``has_jobs``, ``has_pending_jobs``, ``has_quotes``, ``has_invoices``, ``has_addresses``,
    ``ordering``, ``page``, ``page_size``.
    Boolean params accept ``true`` / ``false``. Activity filters use EXISTS, not count joins.

    **Detail** ``GET /api/dashboard/contacts/{ghl_contact_id}/``
    ``ghl_contact_id`` is the GHL contact id (model field ``contact_id``).
    Returns nested addresses, customer submissions (quotes), jobs (with assignees),
    matching invoices, appointments, and a numeric summary block.

    **Update tax exempt** ``PATCH /api/dashboard/contacts/{ghl_contact_id}/``
    Body: ``{"tax_exempt": true}``. Other contact fields are ignored.
    """

    queryset = Contact.objects.all()
    permission_classes = [AllowAny]
    account_lookup = 'account'
    lookup_field = 'contact_id'
    lookup_url_kwarg = 'ghl_contact_id'
    http_method_names = ['get', 'patch', 'head', 'options']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        get_account_from_request(request, allow_superadmin_override=True)

    pagination_class = AdminContactPagination
    filter_backends = [filters.DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_class = AdminContactFilter
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'company_name', 'contact_id']
    ordering_fields = ['date_added', 'last_name', 'first_name', 'id', 'email', 'tax_exempt']
    ordering = ['-date_added', '-id']

    def get_queryset(self):
        qs = super().get_queryset().filter(account__isnull=False)
        location_id = self.request.query_params.get('location_id')
        if location_id:
            qs = qs.filter(location_id=location_id)

        if self.action == 'retrieve':
            submission_qs = CustomerSubmission.objects.select_related(
                'quoted_by', 'location', 'address'
            ).order_by('-created_at')
            job_qs = (
                Job.objects.select_related('quoted_by', 'submission')
                .prefetch_related(
                    Prefetch(
                        'assignments',
                        queryset=JobAssignment.objects.select_related('user'),
                    ),
                    'items',
                )
                .order_by('-created_at')
            )
            appointment_qs = Appointment.objects.select_related(
                'calendar', 'assigned_user'
            ).order_by('-start_time', '-created_at')
            qs = qs.prefetch_related(
                Prefetch('customersubmission_set', queryset=submission_qs),
                Prefetch('jobs', queryset=job_qs),
                Prefetch('contact_location', queryset=Address.objects.order_by('order', 'id')),
                Prefetch('appointments', queryset=appointment_qs),
            )
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        contacts = page if page is not None else list(queryset)
        attach_contact_list_counts(contacts)
        serializer = self.get_serializer(contacts, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    def get_serializer_class(self):
        if self.action in ('update', 'partial_update'):
            return AdminContactTaxExemptUpdateSerializer
        if self.action == 'retrieve':
            return AdminContactDetailSerializer
        return AdminContactListSerializer
