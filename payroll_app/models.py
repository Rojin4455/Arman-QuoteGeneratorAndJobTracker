from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal
import uuid
from service_app.models import User


class EmployeeProfile(models.Model):
    """Extended employee information linked to User"""
    PAY_SCALE_CHOICES = [
        ('hourly', 'Hourly'),
        ('project', 'Project'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee_profile')
    
    # Basic Info
    phone = models.CharField(max_length=20, blank=True, null=True)
    department = models.CharField(max_length=100)
    position = models.CharField(max_length=100)
    timezone = models.CharField(max_length=50, default='America/Chicago')
    
    # Pay Scale Settings
    pay_scale_type = models.CharField(max_length=20, choices=PAY_SCALE_CHOICES)
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True, 
        blank=True,
        help_text="Required if pay_scale_type is 'hourly'"
    )
    
    # Administrator Access
    is_administrator = models.BooleanField(
        default=False,
        help_text="Grants access to view stats, edit records, and manage time entries"
    )
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=[('active', 'Active'), ('inactive', 'Inactive')],
        default='active'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'employee_profiles'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.department}"
    
    def clean(self):
        if self.pay_scale_type == 'hourly' and not self.hourly_rate:
            raise ValidationError({'hourly_rate': 'Hourly rate is required for hourly employees'})


class CollaborationRate(models.Model):
    """Percentage rates for project-based employees based on team size"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='collaboration_rates'
    )
    member_count = models.PositiveIntegerField(
        help_text="Number of team members (1=solo, 2=two members, etc.)"
    )
    percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        help_text="Percentage rate for this team size"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'collaboration_rates'
        unique_together = ['employee', 'member_count']
        ordering = ['employee', 'member_count']
    
    def __str__(self):
        return f"{self.employee.username} - {self.member_count} members: {self.percentage}%"


class TimeEntry(models.Model):
    """Time clock entries for hourly employees"""
    STATUS_CHOICES = [
        ('checked_in', 'Checked In'),
        ('checked_out', 'Checked Out'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='time_entries')
    
    check_in_time = models.DateTimeField()
    check_out_time = models.DateTimeField(null=True, blank=True)
    total_hours = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Calculated automatically on check-out"
    )
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='checked_in')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'time_entries'
        ordering = ['-check_in_time']
    
    def __str__(self):
        return f"{self.employee.username} - {self.check_in_time.strftime('%Y-%m-%d %H:%M')}"
    
    def calculate_hours(self):
        """Calculate total hours worked"""
        if self.check_out_time and self.check_in_time:
            delta = self.check_out_time - self.check_in_time
            hours = Decimal(str(delta.total_seconds() / 3600))
            return hours.quantize(Decimal('0.01'))
        return None


class Payout(models.Model):
    """All payouts for employees (hourly, project, bonuses)"""
    PAYOUT_TYPE_CHOICES = [
        ('hourly', 'Hourly'),
        ('project', 'Project'),
        ('bonus_first_time', 'First Time Bonus'),
        ('bonus_quoted_by', 'Quoted By Bonus'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payouts')
    payout_type = models.CharField(max_length=20, choices=PAYOUT_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    
    # For hourly payouts
    time_entry = models.ForeignKey(
        TimeEntry, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='payouts'
    )
    
    # For project payouts
    job = models.ForeignKey(
        'jobtracker_app.Job', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='payouts'
    )
    project_value = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True
    )
    rate_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        null=True, 
        blank=True
    )
    
    # For manual calculator entries
    project_title = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'payouts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['employee', '-created_at']),
            models.Index(fields=['job', 'employee']),  # For duplicate prevention
        ]
    
    def __str__(self):
        return f"{self.employee.username} - {self.payout_type} - ${self.amount}"


class PayrollSettings(models.Model):
    """Singleton model for payroll bonus settings"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    first_time_bonus_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('15.00'),
        help_text="Bonus for the quoted-by employee on first-time projects"
    )
    quoted_by_bonus_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('2.00'),
        help_text="Bonus for the quoted-by employee on regular (non-first-time) projects"
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'payroll_settings'
        verbose_name = 'Payroll Settings'
        verbose_name_plural = 'Payroll Settings'
    
    def __str__(self):
        return "Payroll Settings"
    
    def save(self, *args, **kwargs):
        # Ensure only one settings record exists
        existing = PayrollSettings.objects.first()
        if existing and existing.pk != self.pk:
            self.pk = existing.pk
        super().save(*args, **kwargs)
    
    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance"""
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj
