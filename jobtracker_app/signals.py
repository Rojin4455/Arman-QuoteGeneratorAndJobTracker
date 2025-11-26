from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Job
from .tasks import handle_completed_job_invoice


@receiver(pre_save, sender=Job)
def _store_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return

    try:
        previous = sender.objects.get(pk=instance.pk)
        instance._previous_status = previous.status
    except sender.DoesNotExist:
        instance._previous_status = None


@receiver(post_save, sender=Job)
def _trigger_invoice_on_completion(sender, instance, created, **kwargs):
    if created:
        return

    previous_status = getattr(instance, "_previous_status", None)
    if instance.status == 'completed' and previous_status != 'completed':
        handle_completed_job_invoice.delay(str(instance.id))

