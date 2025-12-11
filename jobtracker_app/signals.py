from django.db.models.signals import pre_save, post_save, pre_delete
from django.dispatch import receiver

from .models import Job
from .tasks import handle_completed_job_invoice
from service_app.models import Appointment
from .ghl_appointment_sync import (
    create_appointment_in_ghl,
    update_appointment_in_ghl,
    delete_appointment_from_ghl
)


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
        # Check if completion was already processed (prevent duplicate calls)
        if instance.completion_processed:
            print(f"⚠️ Job {instance.id} completion was already processed. Skipping webhook/invoice.")
            return
        
        # Get location_id from job's submission -> contact -> location_id
        # Refresh instance with select_related to avoid extra queries
        try:
            job_with_relations = Job.objects.select_related('submission__contact').get(id=instance.id)
            location_id = None
            if job_with_relations.submission and job_with_relations.submission.contact:
                location_id = job_with_relations.submission.contact.location_id
        except Job.DoesNotExist:
            location_id = None
        
        # Check if location_id matches the required one for webhook
        if location_id == "b8qvo7VooP3JD3dIZU42":
            # Call external webhook instead of invoice handler
            from .tasks import send_job_completion_webhook
            send_job_completion_webhook.delay(str(instance.id))
        else:
            # Use regular invoice handler
            handle_completed_job_invoice.delay(str(instance.id))
        
        # Mark as processed to prevent duplicate calls (even if task fails, we don't want to retry)
        # The task will mark it as processed only on success, but we mark it here to prevent immediate duplicates
        instance.completion_processed = True
        # Use update to avoid triggering signal again
        Job.objects.filter(id=instance.id).update(completion_processed=True)


# Appointment GHL Sync Signals
@receiver(pre_save, sender=Appointment)
def _store_previous_appointment_fields(sender, instance, **kwargs):
    """Store previous appointment fields to detect changes"""
    if not instance.pk:
        instance._previous_fields = {}
        instance._is_new = True
        return
    
    instance._is_new = False
    try:
        previous = sender.objects.get(pk=instance.pk)
        instance._previous_fields = {
            'title': previous.title,
            'appointment_status': previous.appointment_status,
            'start_time': previous.start_time,
            'end_time': previous.end_time,
            'address': previous.address,
            'notes': previous.notes,
            'calendar_id': previous.calendar_id,
            'ghl_contact_id': previous.ghl_contact_id,
            'assigned_user': previous.assigned_user,
            'ghl_assigned_user_id': previous.ghl_assigned_user_id,
        }
    except sender.DoesNotExist:
        instance._previous_fields = {}
        instance._is_new = True


@receiver(post_save, sender=Appointment)
def _sync_appointment_to_ghl(sender, instance, created, **kwargs):
    """Sync appointment to GHL on create or update"""
    # Skip if this is a GHL webhook sync (to avoid infinite loop)
    if getattr(instance, '_skip_ghl_sync', False):
        return
    
    # Skip if appointment already has a GHL ID (not local) - means it came from GHL
    # This prevents syncing appointments that were created/updated from GHL webhooks
    if instance.ghl_appointment_id and not instance.ghl_appointment_id.startswith('local_'):
        return
    
    if created:
        # Only create in GHL if this is a local appointment (starts with 'local_')
        # or if it doesn't have a ghl_appointment_id yet
        if not instance.ghl_appointment_id or instance.ghl_appointment_id.startswith('local_'):
            # Create appointment in GHL
            ghl_appointment_id = create_appointment_in_ghl(instance)
            if ghl_appointment_id:
                # Update the appointment with GHL ID (skip sync to avoid loop)
                instance._skip_ghl_sync = True
                Appointment.objects.filter(id=instance.id).update(ghl_appointment_id=ghl_appointment_id)
                # Refresh instance
                instance.refresh_from_db()
    else:
        # Update appointment in GHL
        previous_fields = getattr(instance, '_previous_fields', {})
        if not previous_fields:
            return
        
        # Detect changed fields
        changed_fields = {}
        for field, old_value in previous_fields.items():
            new_value = getattr(instance, field, None)
            if old_value != new_value:
                changed_fields[field] = new_value
        
        # Only sync if there are changes and appointment exists in GHL
        if changed_fields:
            # If appointment doesn't exist in GHL yet, create it first
            if not instance.ghl_appointment_id or instance.ghl_appointment_id.startswith('local_'):
                ghl_appointment_id = create_appointment_in_ghl(instance)
                if ghl_appointment_id:
                    instance._skip_ghl_sync = True
                    Appointment.objects.filter(id=instance.id).update(ghl_appointment_id=ghl_appointment_id)
                    instance.refresh_from_db()
            else:
                update_appointment_in_ghl(instance, changed_fields=changed_fields)


@receiver(pre_delete, sender=Appointment)
def _delete_appointment_from_ghl(sender, instance, **kwargs):
    """Delete appointment from GHL before deleting from database"""
    # Skip if this is a local appointment
    if not instance.ghl_appointment_id or instance.ghl_appointment_id.startswith('local_'):
        return
    
    # Skip if sync is disabled
    if getattr(instance, '_skip_ghl_sync', False):
        return
    
    delete_appointment_from_ghl(instance)

