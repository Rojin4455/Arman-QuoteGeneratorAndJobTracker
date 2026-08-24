"""
Admin contact hub: list contacts with aggregate counts and retrieve full related graph
(quotes/submissions, jobs, invoices, appointments, addresses).
"""
from django.core.paginator import InvalidPage
from django.db.models import Count, Prefetch, Q
from rest_framework import filters as drf_filters
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


class AdminContactViewSet(AccountScopedQuerysetMixin, ReadOnlyModelViewSet):
    """
    List and retrieve GHL contacts scoped to an account (via auth user, location_id, or default).

    **List** ``GET /api/dashboard/contacts/``
    Optional query params: ``search``, ``location_id``, ``ordering``, ``page``, ``page_size``.

    **Detail** ``GET /api/dashboard/contacts/{ghl_contact_id}/``
    ``ghl_contact_id`` is the GHL contact id (model field ``contact_id``).
    Returns nested addresses, customer submissions (quotes), jobs (with assignees),
    matching invoices, appointments, and a numeric summary block.
    """

    queryset = Contact.objects.all()
    permission_classes = [AllowAny]
    account_lookup = 'account'
    lookup_field = 'contact_id'
    lookup_url_kwarg = 'ghl_contact_id'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        get_account_from_request(request, allow_superadmin_override=True)

    pagination_class = AdminContactPagination
    filter_backends = [drf_filters.SearchFilter, drf_filters.OrderingFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone', 'company_name', 'contact_id']
    ordering_fields = ['date_added', 'last_name', 'first_name', 'id', 'email']
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
        if self.action == 'retrieve':
            return AdminContactDetailSerializer
        return AdminContactListSerializer
