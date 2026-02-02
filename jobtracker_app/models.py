from django.db import models
import uuid
from decimal import Decimal
from quote_app.models import CustomerSubmission
from service_app.models import User, Service
from accounts.models import Contact, Address


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
    DAY_OF_WEEK_CHOICES = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]
    STATUS_CHOICES = [
        ('to_convert', 'Needs Conversion'),
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('service_due', 'Service Due'),
        ('on_the_way', 'On The Way'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('credit_card', 'Credit Card'),
        ('debit_card', 'Debit Card'),
        ('check', 'Check'),
        ('bank_transfer', 'Bank Transfer'),
        ('online_payment', 'Online Payment'),
        ('other', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Optional link when coming from quote flow
    # Changed from OneToOneField to ForeignKey to allow multiple jobs per submission
    # (e.g., recurring jobs created from a single accepted quote)
    submission = models.ForeignKey(
        CustomerSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jobs'  # Changed to plural since one submission can have multiple jobs
    )

    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='low')
    duration_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    scheduled_at = models.DateTimeField(null=True, blank=True)
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Customer info - can be linked to Contact/Address models or provided manually
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jobs',
        help_text="Link to Contact model (optional - if provided, customer info will be populated from this)"
    )
    address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jobs',
        help_text="Link to Address model (optional - if provided, customer_address will be populated from this)"
    )
    # Customer info (freeform - can be manually provided or auto-populated from contact/address)
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
    day_of_week = models.IntegerField(
        choices=DAY_OF_WEEK_CHOICES,
        null=True,
        blank=True,
        help_text="Day of week for weekly recurring jobs (0=Monday, 6=Sunday)"
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, null=True)
    
    # Payment method for completed jobs
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, blank=True, null=True, help_text="Payment method used for this job (only for completed jobs)")
    
    # Track if completion webhook/invoice was already sent
    completion_processed = models.BooleanField(default=False, help_text="True if webhook/invoice was already sent when job was completed")
    
    # Invoice URL from external system (stored in contact's custom field)
    invoice_url = models.URLField(max_length=500, blank=True, null=True, help_text="Invoice URL from external system (G4IXyj5y49rKinuXbnCA custom field)")

    # Series grouping for recurring jobs when creating independent jobs per date
    series_id = models.UUIDField(null=True, blank=True, db_index=True)
    series_sequence = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    # def clean(self):
    #     """Prevent status changes after completion"""
    #     if self.pk:  # Only for existing instances
    #         try:
    #             old_instance = Job.objects.get(pk=self.pk)
    #             if old_instance.status == 'completed' and self.status != 'completed':
    #                 from django.core.exceptions import ValidationError
    #                 raise ValidationError({
    #                     'status': "Cannot change status of a completed job. "
    #                             "Once a job is completed, its status cannot be modified."
    #                 })
    #         except Job.DoesNotExist:
    #             pass

    def __str__(self):
        return self.title or f"Job {self.id}"


class JobServiceItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='items')
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
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


class JobImage(models.Model):
    """Model to store images uploaded for completed jobs"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='job_images/%Y/%m/%d/', blank=True, null=True, help_text="Not used when storing in GHL only")
    caption = models.CharField(max_length=255, blank=True, null=True, help_text="Optional caption for the image")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_job_images')
    ghl_file_id = models.CharField(max_length=255, blank=True, null=True, help_text="GHL media document ID after upload")
    ghl_file_url = models.URLField(max_length=500, blank=True, null=True, help_text="GHL media file URL after upload")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Job Image'
        verbose_name_plural = 'Job Images'

    def __str__(self):
        return f"Image for {self.job.title or self.job.id} - {self.created_at}"