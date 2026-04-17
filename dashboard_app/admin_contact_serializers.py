"""
Serializers for the admin contact hub API (list + full detail with related entities).
"""
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from accounts.models import Address, Contact
from dashboard_app.models import Invoice
from jobtracker_app.models import Job, JobAssignment
from quote_app.models import CustomerSubmission
from service_app.models import Appointment, User


def contact_invoice_filter(contact):
    """Match synced invoices to this GHL contact (id and optional email)."""
    q = Q(contact_id=contact.contact_id)
    email = (contact.email or '').strip()
    if email:
        q |= Q(contact_email__iexact=email)
    return q


class UserBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name']

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class AdminContactAddressSerializer(serializers.ModelSerializer):
    full_address = serializers.CharField(source='get_full_address', read_only=True)

    class Meta:
        model = Address
        fields = [
            'id', 'address_id', 'name', 'order', 'street_address', 'city', 'state',
            'postal_code', 'gate_code', 'number_of_floors', 'property_sqft', 'property_type',
            'full_address',
        ]


class AdminContactSubmissionSerializer(serializers.ModelSerializer):
    quoted_by = UserBriefSerializer(read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True, allow_null=True)

    class Meta:
        model = CustomerSubmission
        fields = [
            'id', 'status', 'house_sqft', 'total_base_price', 'total_adjustments',
            'total_surcharges', 'custom_service_total', 'final_total',
            'quote_surcharge_applicable', 'created_at', 'updated_at', 'expires_at',
            'quoted_by', 'location_id', 'location_name',
        ]


class AdminContactJobAssignmentSerializer(serializers.ModelSerializer):
    user = UserBriefSerializer(read_only=True)

    class Meta:
        model = JobAssignment
        fields = ['id', 'user', 'role', 'created_at']


class AdminContactJobSerializer(serializers.ModelSerializer):
    assignments = AdminContactJobAssignmentSerializer(many=True, read_only=True)
    quoted_by = UserBriefSerializer(read_only=True)
    submission_id = serializers.UUIDField(read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            'id', 'submission_id', 'title', 'description', 'status', 'priority',
            'job_type', 'scheduled_at', 'total_price', 'total_surcharge',
            'discount_type', 'discount_value', 'customer_name', 'customer_phone',
            'customer_email', 'invoice_url', 'quoted_by', 'assignments', 'items_count',
            'created_at', 'updated_at',
        ]

    def get_items_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'items' in obj._prefetched_objects_cache:
            return len(obj.items.all())
        return obj.items.count()


class AdminContactInvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_id', 'invoice_number', 'status', 'name',
            'contact_id', 'contact_name', 'contact_email', 'contact_phone',
            'currency', 'total', 'amount_paid', 'amount_due',
            'issue_date', 'due_date', 'created_at', 'updated_at', 'location_id',
            'is_overdue',
        ]

    def get_is_overdue(self, obj):
        if obj.status in ('paid', 'void'):
            return False
        if not obj.due_date or not (obj.amount_due is not None and obj.amount_due > 0):
            return False
        return obj.due_date < timezone.now()


class AdminContactAppointmentSerializer(serializers.ModelSerializer):
    assigned_user = UserBriefSerializer(read_only=True)
    calendar_name = serializers.CharField(source='calendar.name', read_only=True, allow_null=True)

    class Meta:
        model = Appointment
        fields = [
            'id', 'ghl_appointment_id', 'title', 'appointment_status', 'estimate_status',
            'location_id', 'address', 'source', 'notes', 'start_time', 'end_time',
            'assigned_user', 'calendar_name', 'created_at', 'updated_at',
        ]


class AdminContactListSerializer(serializers.ModelSerializer):
    """Paginated list row; counts come from queryset annotations in the view."""
    submissions_count = serializers.IntegerField(read_only=True, default=0)
    jobs_count = serializers.IntegerField(read_only=True, default=0)
    addresses_count = serializers.IntegerField(read_only=True, default=0)
    pending_jobs_count = serializers.IntegerField(read_only=True, default=0)
    appointments_count = serializers.IntegerField(read_only=True, default=0)
    invoices_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Contact
        fields = [
            'id', 'contact_id', 'first_name', 'last_name', 'email', 'phone',
            'company_name', 'country', 'location_id', 'date_added', 'dnd', 'tags',
            'submissions_count', 'jobs_count', 'addresses_count', 'pending_jobs_count',
            'appointments_count', 'invoices_count',
        ]


class AdminContactDetailSerializer(serializers.ModelSerializer):
    addresses = AdminContactAddressSerializer(source='contact_location', many=True, read_only=True)
    submissions = AdminContactSubmissionSerializer(
        source='customersubmission_set', many=True, read_only=True
    )
    jobs = AdminContactJobSerializer(many=True, read_only=True)
    invoices = serializers.SerializerMethodField()
    appointments = AdminContactAppointmentSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            'id', 'contact_id', 'account_id', 'first_name', 'last_name', 'email', 'phone',
            'company_name', 'country', 'location_id', 'date_added', 'dnd', 'tags',
            'custom_fields', 'timestamp',
            'addresses', 'submissions', 'jobs', 'invoices', 'appointments', 'summary',
        ]

    def get_invoices(self, obj):
        if not obj.account_id:
            return []
        qs = (
            Invoice.objects.filter(account_id=obj.account_id)
            .filter(contact_invoice_filter(obj))
            .order_by('-created_at')[:250]
        )
        return AdminContactInvoiceSerializer(qs, many=True).data

    def get_summary(self, obj):
        submissions = list(obj.customersubmission_set.all())
        jobs = list(obj.jobs.all())
        appointments = list(obj.appointments.all())

        open_quote_statuses = {'draft', 'responses_completed', 'packages_selected'}
        open_quotes = [s for s in submissions if s.status in open_quote_statuses]

        pending_statuses = {
            'pending', 'confirmed', 'on_the_way', 'service_due', 'to_convert',
            'in_progress', 'onhold',
        }
        pending_jobs = [j for j in jobs if j.status in pending_statuses]

        inv_base = Invoice.objects.filter(account_id=obj.account_id).filter(contact_invoice_filter(obj)) if obj.account_id else Invoice.objects.none()
        inv_agg = inv_base.aggregate(
            total_invoiced=Sum('total'),
            total_outstanding=Sum('amount_due'),
        )

        return {
            'submissions_total': len(submissions),
            'open_quotes_count': len(open_quotes),
            'jobs_total': len(jobs),
            'pending_jobs_count': len(pending_jobs),
            'invoices_total': inv_base.count(),
            'appointments_total': len(appointments),
            'invoiced_amount_sum': float(inv_agg['total_invoiced'] or 0),
            'invoice_balance_sum': float(inv_agg['total_outstanding'] or 0),
        }
