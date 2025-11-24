from celery import shared_task
from django.utils import timezone
from .models import Job


@shared_task
def update_jobs_to_service_due():
    """
    Update jobs with status 'confirmed' to 'service_due' 
    when their scheduled_at time has passed.
    """
    now = timezone.now()
    
    # Find all confirmed jobs where scheduled_at has passed
    jobs_to_update = Job.objects.filter(
        status='confirmed',
        scheduled_at__lte=now,
        scheduled_at__isnull=False
    )
    
    # Update status to service_due
    count = jobs_to_update.update(status='service_due')
    
    print(f"Updated {count} job(s) from 'confirmed' to 'service_due'")
    return f"Updated {count} job(s)"

