from datetime import datetime
import requests

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

def _process_invoice_payload(data, job_id=None):
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

        # Save invoice URL to job if job_id is provided
        if job_id and invoice_id:
            try:
                invoice_url = f"https://workorder.theservicepilot.com/invoice/{invoice_id}/"
                Job.objects.filter(id=job_id).update(invoice_url=invoice_url)
                print(f"Invoice URL saved to job {job_id}: {invoice_url}")
            except Exception as e:
                print(f"Error saving invoice URL to job {job_id}: {str(e)}")

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


def _mark_job_completion_processed(job_id):
    """Helper function to mark job as completion processed"""
    try:
        Job.objects.filter(id=job_id).update(completion_processed=True)
    except Exception as e:
        print(f"Error marking job {job_id} as processed: {str(e)}")


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
        result = _process_invoice_payload(payload, job_id=str(job_id))
        
        # Mark job as processed only if invoice was successfully created
        if result and not result.get("error"):
            _mark_job_completion_processed(job_id)
        
        return result
    except Exception as e:
        print(f"Error handling completed job invoice: {str(e)}")
        return {"error": str(e)}


@shared_task
def send_job_completion_webhook(job_id):
    """
    Send job completion webhook to external API when location_id matches.
    
    Args:
        job_id: UUID of the completed job
        
    Returns:
        dict: Response from webhook API or error
    """
    try:
        job = (
            Job.objects.select_related('submission__contact')
            .prefetch_related('items__service')
            .filter(id=job_id)
            .first()
        )
        if not job:
            return {"error": f"Job {job_id} not found"}
        
        # Get location_id from job's submission -> contact -> location_id
        location_id = None
        if job.submission and job.submission.contact:
            location_id = job.submission.contact.location_id
        else:
            # Fallback to credentials if submission/contact not available
            credentials = GHLAuthCredentials.objects.first()
            if credentials:
                location_id = credentials.location_id
        
        if not location_id:
            return {"error": "Location ID not found in job submission contact or credentials"}
        
        # Check if location_id matches the required one
        if location_id != "b8qvo7VooP3JD3dIZU42":
            return {"error": f"Location ID {location_id} does not match required location"}
        
        # Build selected_services from job items
        selected_services = []
        for item in job.items.all():
            service_data = {
                "id": str(item.service.id) if item.service else None,
                "name": item.service.name if item.service else item.custom_name or "Custom Service",
                "price": float(item.price)
            }
            selected_services.append(service_data)
        
        # Build webhook payload
        payload = {
            "customer_email": job.customer_email or "",
            "selected_services": selected_services,
        }
        
        # Add optional fields
        if job.customer_name:
            payload["customer_name"] = job.customer_name
        
        if job.customer_address:
            payload["customer_address"] = job.customer_address
        
        if location_id:
            payload["location_id"] = location_id
        
        # Validate required fields
        if not payload.get("customer_email"):
            return {"error": "customer_email is required"}
        
        if not payload.get("selected_services"):
            return {"error": "selected_services is required"}
        
        # Call external webhook API
        url = "https://workorder.theservicepilot.com/api/webhook/"
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        
        if response.status_code in [200, 201]:
            print(f"✅ Successfully sent job completion webhook for job {job_id}")
            # Mark job as processed only on success
            Job.objects.filter(id=job_id).update(completion_processed=True)
            return {
                "success": True,
                "status_code": response.status_code,
                "response": response.json() if response.content else {}
            }
        else:
            print(f"❌ Failed to send webhook: {response.status_code} - {response.text}")
            # Don't mark as processed if webhook failed - allow retry
            return {
                "error": f"Webhook API returned status {response.status_code}",
                "status_code": response.status_code,
                "response": response.text
            }
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Error calling webhook API: {str(e)}")
        return {"error": f"Request error: {str(e)}"}
    except Exception as e:
        print(f"❌ Error sending job completion webhook: {str(e)}")
        return {"error": str(e)}