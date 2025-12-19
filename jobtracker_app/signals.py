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
    print(f"🔔 [SIGNAL] post_save triggered | job_id={instance.id} | created={created}")

    if created:
        print("🆕 Job was just created — skipping completion logic")
        return

    previous_status = getattr(instance, "_previous_status", None)
    print(f"📊 Job status check | previous={previous_status} | current={instance.status}")

    # --------------------------------------------------
    # Only act when job transitions to 'completed'
    # --------------------------------------------------
    if instance.status == 'completed' and previous_status != 'completed':
        print(f"✅ Job transitioned to COMPLETED | job_id={instance.id}")

        # --------------------------------------------------
        # Prevent duplicate processing
        # --------------------------------------------------
        if instance.completion_processed:
            print(
                f"⚠️ Completion already processed — skipping | "
                f"job_id={instance.id}"
            )
            return

        # --------------------------------------------------
        # Resolve location_id
        # --------------------------------------------------
        location_id = "b8qvo7VooP3JD3dIZU42"
        try:
            print("🔍 Fetching job with submission/contact for location_id")
            job_with_relations = (
                Job.objects
                .select_related('submission__contact')
                .get(id=instance.id)
            )

            if job_with_relations.submission and job_with_relations.submission.contact:
                location_id = job_with_relations.submission.contact.location_id
                print(f"📍 location_id resolved: {location_id}")
            else:
                print("⚠️ No submission/contact found for job")

        except Job.DoesNotExist:
            print("❌ Job not found while resolving location_id")

        # --------------------------------------------------
        # Decide which async task to trigger
        # --------------------------------------------------
        REQUIRED_LOCATION_ID = "b8qvo7VooP3JD3dIZU42"
        print(
            f"🔎 Evaluating routing | "
            f"location_id={location_id} | required={REQUIRED_LOCATION_ID}"
        )

        if location_id == REQUIRED_LOCATION_ID:
            print(
                f"🌐 Routing to EXTERNAL WEBHOOK | "
                f"job_id={instance.id}"
            )
            from .tasks import send_job_completion_webhook
            send_job_completion_webhook.delay(str(instance.id))
        else:
            print(
                f"🧾 Routing to INVOICE HANDLER | "
                f"job_id={instance.id}"
            )
            handle_completed_job_invoice.delay(str(instance.id))

        # --------------------------------------------------
        # Mark completion as processed
        # --------------------------------------------------
        print(
            f"🧷 Marking job as completion_processed=True | "
            f"job_id={instance.id}"
        )

        instance.completion_processed = True
        Job.objects.filter(id=instance.id).update(completion_processed=True)

    else:
        print(
            f"No action taken | "
            f"status={instance.status} | previous={previous_status}"
        )


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
    """
    Sync appointment updates to GHL.
    All appointments originate from GHL webhooks, so we only sync updates back to GHL.
    The _skip_ghl_sync flag prevents loops when updates come from GHL webhooks.
    """
    # Skip if this is a GHL webhook sync (to avoid infinite loop)
    # This flag is set in create_or_update_appointment_from_ghl when processing webhooks
    if getattr(instance, '_skip_ghl_sync', False):
        return
    
    # Skip creation - all appointments should come from GHL webhooks
    if created:
        # If somehow an appointment is created without a GHL ID, log a warning
        if not instance.ghl_appointment_id:
            print(f"⚠️ Appointment {instance.id} created without ghl_appointment_id. Appointments should come from GHL webhooks.")
        return
    
    # Handle updates - sync changes back to GHL
    previous_fields = getattr(instance, '_previous_fields', {})
    if not previous_fields:
        return
    
    # Detect changed fields
    changed_fields = {}
    for field, old_value in previous_fields.items():
        new_value = getattr(instance, field, None)
        if old_value != new_value:
            changed_fields[field] = new_value
    
    # Only sync if there are changes and appointment has a GHL ID
    if changed_fields and instance.ghl_appointment_id:
        # Update appointment in GHL
        update_appointment_in_ghl(instance, changed_fields=changed_fields)
    elif changed_fields and not instance.ghl_appointment_id:
        print(f"⚠️ Cannot sync appointment {instance.id} to GHL: missing ghl_appointment_id")


@receiver(pre_delete, sender=Appointment)
def _delete_appointment_from_ghl(sender, instance, **kwargs):
    """Delete appointment from GHL before deleting from database"""
    # Skip if sync is disabled (e.g., during webhook processing)
    if getattr(instance, '_skip_ghl_sync', False):
        return
    
    # Only delete if appointment exists in GHL (has a real GHL appointment ID)
    if instance.ghl_appointment_id and not instance.ghl_appointment_id.startswith('local_'):
        delete_appointment_from_ghl(instance)

