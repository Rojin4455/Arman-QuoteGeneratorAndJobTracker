import requests
from celery import shared_task
from django.utils import timezone

from dashboard_app.services import invoice_sync
from dashboard_app.models import Invoice
from accounts.models import GHLAuthCredentials

# TruShine location: forward GHL InvoicePaid to workorder invoice service
WORKORDER_INVOICE_PAID_LOCATION_ID = "b8qvo7VooP3JD3dIZU42"
WORKORDER_INVOICE_PAID_URL = "https://workorder.theservicepilot.com/api/webhook/invoice-paid/"


@shared_task
def notify_workorder_invoice_paid(ghl_invoice_id, status="paid"):
    """
    Notify workorder.theservicepilot.com that a GHL invoice was paid.
    Only used for TruShine (location b8qvo7VooP3JD3dIZU42).
    """
    if not ghl_invoice_id:
        return {"success": False, "error": "ghl_invoice_id is required"}

    payload = {
        "ghl_invoice_id": str(ghl_invoice_id),
        "status": status or "paid",
    }
    headers = {"Content-Type": "application/json"}

    try:
        print(
            f"🌐 [INVOICE PAID] POST {WORKORDER_INVOICE_PAID_URL} "
            f"payload={payload}"
        )
        response = requests.post(
            WORKORDER_INVOICE_PAID_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        print(
            f"📨 [INVOICE PAID] status={response.status_code} body={response.text[:500]}"
        )
        return {
            "success": response.status_code in (200, 201),
            "status_code": response.status_code,
            "response": response.text[:500],
            "ghl_invoice_id": ghl_invoice_id,
        }
    except requests.exceptions.RequestException as e:
        print(f"🚨 [INVOICE PAID] Request error for ghl_invoice_id={ghl_invoice_id}: {e}")
        return {"success": False, "error": str(e), "ghl_invoice_id": ghl_invoice_id}
    except Exception as e:
        print(f"🔥 [INVOICE PAID] Unexpected error for ghl_invoice_id={ghl_invoice_id}: {e}")
        return {"success": False, "error": str(e), "ghl_invoice_id": ghl_invoice_id}


@shared_task
def sync_single_invoice_task(location_id, invoice_id):
    """
    Celery task to sync a single invoice by ID.
    """
    try:
        invoice = invoice_sync.sync_invoices(location_id, invoice_id)
        if invoice:
            # Return a JSON-serializable dictionary instead of the model instance
            return {
                "success": True,
                "invoice_id": invoice.invoice_id,
                "invoice_number": invoice.invoice_number,
                "location_id": location_id
            }
        else:
            return {
                "success": False,
                "message": "Invoice not found or failed to sync",
                "invoice_id": invoice_id,
                "location_id": location_id
            }
    except Exception as e:
        print(f"Error syncing invoice {invoice_id} for location {location_id}: {str(e)}")
        raise

@shared_task
def delete_invoice_task(invoice_id):
    """
    Celery task to delete an invoice from the database.
    """
    try:
        invoice = Invoice.objects.filter(invoice_id=invoice_id).first()
        if invoice:
            invoice.delete()
            print(f"Invoice {invoice_id} deleted successfully")
            return {"success": True, "invoice_id": invoice_id}
        else:
            print(f"Invoice {invoice_id} not found in database")
            return {"success": False, "message": "Invoice not found", "invoice_id": invoice_id}
    except Exception as e:
        print(f"Error deleting invoice {invoice_id}: {str(e)}")
        raise

@shared_task
def sync_all_invoices_periodic():
    """
    Periodic Celery task to sync all invoices from GHL for all locations.
    Runs every 10 minutes via Celery Beat.
    """
    try:
        print("🔄 [PERIODIC SYNC] Starting periodic invoice sync for all locations...")
        
        # Get all active GHL credentials (each has a location_id)
        credentials_list = GHLAuthCredentials.objects.all()
        
        if not credentials_list.exists():
            print("⚠️ [PERIODIC SYNC] No GHL credentials found. Skipping sync.")
            return {
                "success": False,
                "message": "No GHL credentials found",
                "locations_synced": 0
            }
        
        total_synced = 0
        total_created = 0
        total_updated = 0
        total_deleted = 0
        errors = []
        
        for credentials in credentials_list:
            location_id = credentials.location_id
            if not location_id:
                print(f"⚠️ [PERIODIC SYNC] Credentials {credentials.id} has no location_id. Skipping.")
                continue
            
            try:
                print(f"📦 [PERIODIC SYNC] Syncing invoices for location_id: {location_id}")
                result = invoice_sync.sync_invoices(location_id, invoice_id=None)
                
                if result:
                    total_synced += result.get("total", 0)
                    total_created += result.get("created", 0)
                    total_updated += result.get("updated", 0)
                    total_deleted += result.get("deleted", 0)
                    print(f"✅ [PERIODIC SYNC] Location {location_id}: {result.get('total', 0)} invoices synced")
                else:
                    print(f"⚠️ [PERIODIC SYNC] Location {location_id}: No result returned from sync")
                    
            except Exception as e:
                error_msg = f"Error syncing invoices for location {location_id}: {str(e)}"
                print(f"❌ [PERIODIC SYNC] {error_msg}")
                errors.append(error_msg)
                continue
        
        summary = {
            "success": len(errors) == 0,
            "locations_processed": credentials_list.count(),
            "total_synced": total_synced,
            "total_created": total_created,
            "total_updated": total_updated,
            "total_deleted": total_deleted,
            "errors": errors if errors else None
        }
        
        print(f"✅ [PERIODIC SYNC] Completed. Summary: {summary}")
        return summary
        
    except Exception as e:
        error_msg = f"Critical error in periodic invoice sync: {str(e)}"
        print(f"❌ [PERIODIC SYNC] {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": error_msg,
            "locations_synced": 0
        }


# Statuses that can become overdue (not yet paid, void, or already overdue)
STATUSES_ELIGIBLE_FOR_OVERDUE = ['sent']


@shared_task
def mark_overdue_invoices_task():
    """
    Celery task to mark invoices as overdue when due_date has passed.
    Runs every hour via Celery Beat. Only updates invoices that are still
    in draft/sent/payment_processing/partial (not paid, void, or already overdue).
    """
    try:
        now = timezone.now()
        qs = Invoice.objects.filter(
            due_date__lt=now,
            due_date__isnull=False,
            status__in=STATUSES_ELIGIBLE_FOR_OVERDUE,
        )
        count = qs.update(status='overdue')
        if count:
            print(f"✅ [OVERDUE] Marked {count} invoice(s) as overdue.")
        return {"success": True, "marked_overdue": count}
    except Exception as e:
        print(f"❌ [OVERDUE] Error marking overdue invoices: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": str(e), "marked_overdue": 0}