from django.db import models
import uuid
from decimal import Decimal
from quote_app.models import CustomerSubmission
from service_app.models import User


class JTService(models.Model):
    """User-defined service template used when creating jobs directly."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    default_duration_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    default_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Job(models.Model):
    """Job record. Can be created from accepted quote or directly from portal."""
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]
    JOB_TYPE_CHOICES = [
        ('one_time', 'One Time'),
        ('recurring', 'Recurring'),
    ]
    REPEAT_UNIT_CHOICES = [
        ('day', 'Day'),
        ('week', 'Week'),
        ('month', 'Month'),
        ('quarter', 'Quarter'),
        ('semi_annual', 'Semi-Annual'),
        ('year', 'Year'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('accepted', 'Accepted'),
        ('scheduled', 'Scheduled'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Optional link when coming from quote flow
    submission = models.OneToOneField(
        CustomerSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='job'
    )

    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='low')
    duration_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    scheduled_at = models.DateTimeField(null=True, blank=True)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Customer info (freeform for now)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_phone = models.CharField(max_length=30, blank=True, null=True)
    customer_email = models.EmailField(blank=True, null=True)
    customer_address = models.TextField(blank=True, null=True)
    ghl_contact_id = models.CharField(max_length=255, blank=True, null=True)

    quoted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='quoted_jobs')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_jobs')
    created_by_email = models.EmailField(max_length=255, null=True, blank=True)

    job_type = models.CharField(max_length=20, choices=JOB_TYPE_CHOICES, default='one_time')
    repeat_every = models.PositiveIntegerField(null=True, blank=True)
    repeat_unit = models.CharField(max_length=20, choices=REPEAT_UNIT_CHOICES, null=True, blank=True)
    occurrences = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title or f"Job {self.id}"


class JobServiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='items')
    service = models.ForeignKey(JTService, on_delete=models.SET_NULL, null=True, blank=True)
    custom_name = models.CharField(max_length=255, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    duration_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))

    created_at = models.DateTimeField(auto_now_add=True)


class JobAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='assignments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class JobOccurrence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='schedule_occurrences')
    scheduled_at = models.DateTimeField()
    sequence = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)