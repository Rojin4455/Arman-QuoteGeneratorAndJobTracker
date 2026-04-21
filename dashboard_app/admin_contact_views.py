"""
Admin contact hub: list contacts with aggregate counts and retrieve full related graph
(quotes/submissions, jobs, invoices, appointments, addresses).
"""
from django.db.models import (
    Count,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from rest_framework import filters as drf_filters
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
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
    ordering = ['-date_added']

    def get_queryset(self):
        qs = super().get_queryset().filter(account__isnull=False)
        location_id = self.request.query_params.get('location_id')
        if location_id:
            qs = qs.filter(location_id=location_id)

        if self.action == 'list':
            invoices_sq = (
                Invoice.objects.filter(
                    account_id=OuterRef('account_id'),
                    contact_id=OuterRef('contact_id'),
                )
                .values('account_id')
                .annotate(cnt=Count('id'))
                .values('cnt')[:1]
            )
            qs = qs.annotate(
                submissions_count=Count('customersubmission', distinct=True),
                jobs_count=Count('jobs', distinct=True),
                addresses_count=Count('contact_location', distinct=True),
                pending_jobs_count=Count(
                    'jobs',
                    filter=Q(jobs__status__in=_NON_TERMINAL_JOB_STATUSES),
                    distinct=True,
                ),
                appointments_count=Count('appointments', distinct=True),
                invoices_count=Coalesce(
                    Subquery(invoices_sq, output_field=IntegerField()),
                    Value(0),
                ),
            )
        elif self.action == 'retrieve':
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

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AdminContactDetailSerializer
        return AdminContactListSerializer
