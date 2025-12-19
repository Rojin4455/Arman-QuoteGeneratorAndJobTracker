from celery import shared_task
from dashboard_app.services import invoice_sync
from dashboard_app.models import Invoice

# @shared_task
# def sync_invoices_daily():
#     invoice_sync()

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