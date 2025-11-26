from datetime import datetime

from celery import shared_task
from django.utils import timezone

from accounts.models import GHLAuthCredentials
from .helpers import (
    build_invoice_payload_from_job,
    create_invoice,
    search_ghl_contact,
    send_invoice,
    update_contact,
)
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

def _process_invoice_payload(data):
    customer_email = data.get("customer_email")
    customer_name = data.get("customer_name")
    services = data.get("selected_services", [])
    customer_address = data.get("customer_address")

    if not customer_email:
        print("No customer email in invoice payload.")
        return {"error": "Customer email missing"}
    
    credentials = GHLAuthCredentials.objects.first()

    # Search contact
    contacts = search_ghl_contact(credentials.access_token, customer_email, credentials.location_id)
    if not contacts:
        print(f"No GHL contact found for email: {customer_email}")
        return {"error": f"Contact not found for {customer_email}"}

    contact_id = contacts[0].get("id") or contacts[0].get("_id")

    companyName = contacts[0].get("companyName")
    phoneNo = contacts[0].get("phone")
    contactName = contacts[0].get("contactName")
    address = {
        "address1": contacts[0].get("address1"),
        "city": contacts[0].get("city"),
        "state": contacts[0].get("state"),
        "postalCode": contacts[0].get("postalCode"),
        "country": contacts[0].get("country"),
    }

    print("companyName", companyName)
    tags = contacts[0].get("tags")
    if not contact_id:
        print("Contact found, but ID missing.")
        return {"error": "Invalid contact data"}
    
    print("Contact found,", contact_id)
    invoice_name = f"Invoice for {customer_name or customer_email} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    response = create_invoice(
        name=invoice_name,
        contact_id=contact_id,
        services=services,
        credentials=credentials,
        customer_address=customer_address,
        address=address,
        companyName=companyName,
        phoneNo=phoneNo,
        contactName=contactName,
    )

    print("Invoice response:", response)
    print("Tags before check:", tags)
    if response and not response.get("error"):
        invoice_id = response.get("_id")

        existing_tags = tags if isinstance(tags, list) else []
        print("Existing tags:", existing_tags)

        try:
            if "card authorized" not in [t.lower() for t in existing_tags]:
                print("Card not authorized → sending invoice...")
                send_resp = send_invoice(invoice_id)
                print("Send invoice response:", send_resp)
            else:
                print("Card authorized → skipping invoice send.")
                send_resp = "skipped"
        except Exception as e:
            print("Error sending invoice:", e)
            send_resp = None

        updated_tags = list(set(existing_tags + ["Invoice Created"]))
        payload = {"tags": updated_tags}
        update_resp = update_contact(contact_id, payload)
        print("Contact update response:", update_resp)

        return {
            "invoice": response,
            "contact_update": update_resp,
            "invoice_send": send_resp
        }

    return response


@shared_task
def handle_webhook_event(data):
    try:
        return _process_invoice_payload(data)
    except Exception as e:
        print(f"Error handling webhook event: {str(e)}")
        return {"error": str(e)}


@shared_task
def handle_completed_job_invoice(job_id):
    try:
        job = (
            Job.objects.select_related('submission')
            .prefetch_related('items__service')
            .filter(id=job_id)
            .first()
        )
        if not job:
            return {"error": f"Job {job_id} not found"}

        payload = build_invoice_payload_from_job(job)
        return _process_invoice_payload(payload)
    except Exception as e:
        print(f"Error handling completed job invoice: {str(e)}")
        return {"error": str(e)}