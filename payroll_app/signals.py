from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.core.exceptions import ValidationError
from decimal import Decimal
from jobtracker_app.models import Job
from service_app.models import User
from .models import Payout, PayrollSettings, EmployeeProfile, CollaborationRate


@receiver(pre_save, sender=Job)
def prevent_job_status_change_after_completion(sender, instance, **kwargs):
    """Prevent changing status once job is marked as completed"""
    if instance.pk:  # Only for updates
        try:
            old_instance = Job.objects.get(pk=instance.pk)
            # If job was already completed, prevent status change
            if old_instance.status == 'completed' and instance.status != 'completed':
                raise ValidationError(
                    "Cannot change status of a completed job. "
                    "Once a job is completed, its status cannot be modified."
                )
        except Job.DoesNotExist:
            pass


@receiver(pre_save, sender=Job)
def create_project_payouts_on_completion(sender, instance, **kwargs):
    """Create payouts when job status changes to completed"""
    if instance.pk:  # Only for updates
        try:
            old_instance = Job.objects.get(pk=instance.pk)
            # Check if status changed to 'completed'
            if old_instance.status != 'completed' and instance.status == 'completed':
                _create_project_payouts(instance)
        except Job.DoesNotExist:
            pass


def _create_project_payouts(job):
    """Helper function to create payouts for all assigned employees"""
    from service_app.models import User
    
    # Get assigned employees
    assignments = job.assignments.all().select_related('user')
    
    if not assignments.exists():
        return  # No employees assigned, skip payout creation
    
    # Get project value
    project_value = job.total_price or Decimal('0.00')
    
    if project_value <= 0:
        return  # No value, skip payout creation
    
    # Get payroll settings
    settings = PayrollSettings.get_settings()
    
    # Determine if first-time project
    is_first_time = (job.job_type == 'one_time')
    
    # Get number of assigned employees
    employee_count = assignments.count()
    
    # Create payouts for each assigned employee
    for assignment in assignments:
        employee = assignment.user
        
        # Check if employee has profile and is project-based
        try:
            profile = employee.employee_profile
            if profile.pay_scale_type != 'project':
                continue  # Skip hourly employees
        except EmployeeProfile.DoesNotExist:
            continue  # Skip employees without profile
        
        # Check if payout already exists (duplicate prevention)
        existing_payout = Payout.objects.filter(
            job=job,
            employee=employee,
            payout_type='project'
        ).first()
        
        if existing_payout:
            continue  # Payout already exists, skip
        
        # Get collaboration rate for this team size
        try:
            collaboration_rate = CollaborationRate.objects.get(
                employee=employee,
                member_count=employee_count
            )
            rate_percentage = collaboration_rate.percentage
        except CollaborationRate.DoesNotExist:
            # If no rate found for this team size, skip this employee
            continue
        
        # Calculate payout amount (per person, not divided)
        amount = (project_value * rate_percentage) / Decimal('100')
        amount = amount.quantize(Decimal('0.01'))
        
        # Create project payout
        Payout.objects.create(
            employee=employee,
            payout_type='project',
            amount=amount,
            job=job,
            project_value=project_value,
            rate_percentage=rate_percentage,
            notes=f"Automated payout for job: {job.title or job.id}"
        )
    
    # Create bonus payout for quoted_by person if exists
    if job.quoted_by:
        quoted_by_employee = job.quoted_by
        
        # Check if bonus payout already exists
        bonus_type = 'bonus_first_time' if is_first_time else 'bonus_quoted_by'
        existing_bonus = Payout.objects.filter(
            job=job,
            employee=quoted_by_employee,
            payout_type=bonus_type
        ).first()
        
        if not existing_bonus:
            # Get bonus percentage
            if is_first_time:
                bonus_percentage = settings.first_time_bonus_percentage
            else:
                bonus_percentage = settings.quoted_by_bonus_percentage
            
            # Calculate bonus amount
            bonus_amount = (project_value * bonus_percentage) / Decimal('100')
            bonus_amount = bonus_amount.quantize(Decimal('0.01'))
            
            # Create bonus payout
            Payout.objects.create(
                employee=quoted_by_employee,
                payout_type=bonus_type,
                amount=bonus_amount,
                job=job,
                project_value=project_value,
                rate_percentage=bonus_percentage,
                notes=f"Automated {bonus_type} bonus for job: {job.title or job.id}"
            )


@receiver(post_save, sender=User)
def create_employee_profile(sender, instance, created, **kwargs):
    """Automatically create EmployeeProfile when a User is created"""
    if created:
        # Check if profile already exists (prevent duplicates)
        if not hasattr(instance, 'employee_profile'):
            EmployeeProfile.objects.create(
                user=instance,
                department='General',
                position='Employee',
                pay_scale_type='project',  # Default to project-based, can be changed later
                status='active'
            )

