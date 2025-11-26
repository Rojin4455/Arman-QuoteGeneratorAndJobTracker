import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from accounts.models import GHLAuthCredentials, Contact


def get_or_create_product(access_token, location_id, product_name, custom_data=None):
    """
    Look up an existing product by name within the provided GHL location.
    If it does not exist, create a lightweight SERVICE product so the invoice
    payload can reference it.
    """
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}',
        'Version': '2021-07-28'
    }

    search_url = (
        "https://services.leadconnectorhq.com/products/"
        f"?locationId={location_id}&search={product_name}"
    )

    try:
        response = requests.get(search_url, headers=headers)
        if response.status_code == 200:
            products = response.json().get('products', [])
            if products:
                product = products[0]
                return {
                    "productId": product.get('_id'),
                    "priceId": product.get("prices", [{}])[0].get("_id")
                }
    except Exception as exc:
        print(f"Error searching for product '{product_name}': {exc}")

    # Fallback: create the product so invoices can continue
    return create_product(access_token, location_id, product_name, custom_data or {})


def create_product(access_token, location_id, product_name, custom_data=None):
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Version': '2021-07-28'
    }

    custom_data = custom_data or {}
    try:
        price = float(custom_data.get("price") or custom_data.get("Price") or 0)
    except (TypeError, ValueError):
        price = 0.0

    description = custom_data.get("description") or f"Auto-created product: {product_name}"
    slug = (
        product_name.lower()
        .replace(" ", "-")
        .replace("_", "-")
    )

    product_payload = {
        "name": product_name,
        "locationId": location_id,
        "description": description,
        "productType": "SERVICE",
        "availableInStore": True,
        "isTaxesEnabled": False,
        "isLabelEnabled": False,
        "slug": slug,
        "prices": [
            {
                "name": "Default",
                "amount": price,
                "currency": "USD"
            }
        ]
    }

    url = "https://services.leadconnectorhq.com/products/"

    try:
        response = requests.post(url, headers=headers, json=product_payload)
        print(response.json(), 'product_create_response')
        if response.status_code in (200, 201):
            product = response.json()
            return {"productId": product.get('_id')}
        print(f"Failed to create product {product_name}: {response.status_code} - {response.text}")
    except Exception as exc:
        print(f"Error creating product '{product_name}': {exc}")

    return None


def build_invoice_payload_from_job(job):
    """
    Construct the payload expected by the invoice flow based on a Job instance.
    """
    items = job.items.all()
    services = []

    for item in items:
        name = None
        description = ""
        if item.service:
            name = item.service.name
            description = getattr(item.service, "description", "") or ""
        if not name:
            name = item.custom_name or job.title or "Service"
        description = description or job.description or ""

        services.append({
            "name": name,
            "description": description,
            "quantity": 1,
            "price": float(item.price or 0),
        })

    if not services:
        services.append({
            "name": job.title or "Service",
            "description": job.description or "",
            "quantity": 1,
            "price": float(job.total_price or 0),
        })

    contact_email = job.customer_email
    contact_name = job.customer_name
    contact_phone = job.customer_phone
    company_name = None

    if job.ghl_contact_id and not contact_email:
        contact = Contact.objects.using('external').filter(contact_id=job.ghl_contact_id).first()
        if contact:
            contact_email = contact_email or contact.email
            contact_name = contact_name or f"{contact.first_name or ''} {contact.last_name or ''}".strip()
            contact_phone = contact_phone or contact.phone

    payload = {
        "customer_email": contact_email,
        "customer_name": contact_name,
        "customer_address": job.customer_address,
        "selected_services": services,
        "phone": contact_phone,
        "company_name": company_name,
    }

    return payload


def update_contact(contact_id, data):
    url = f'https://services.leadconnectorhq.com/contacts/{contact_id}'
    credentials = GHLAuthCredentials.objects.first()
    print(credentials, 'creee')

    headers = {
        'Authorization': f'Bearer {credentials.access_token}',
        'Content-Type': 'application/json',
        'Version':'2021-07-28'
    }

    try:
        response = requests.put(url, headers=headers, json=data)
        print(response.json(), 'responseeeeee')
        return response.json()
    except Exception as e:
        print(e, 'errorrr')
        return {'error':'Error while updating ghl contact'}


def search_ghl_contact(access_token, email, locationId):
    url = 'https://services.leadconnectorhq.com/contacts/'
    response = requests.get(
        url,
        headers={
            'Accept': 'application/json',
            'Authorization': f"Bearer {access_token}",
            'Version': '2021-07-28'
        },
        params={"query": email, "locationId": locationId}
    )
    print("Raw response:", response.status_code, response.text, response.json())
    return response.json().get("contacts", [])



def create_invoice(name, contact_id, services, credentials, customer_address, address, companyName, phoneNo, contactName):
    """
    Create an invoice in GHL for the given contact.

    Args:
        contact_id (str): GHL contact ID
        location_id (str): GHL location ID
        services (list): List of services (product objects)
        credentials: GHLAuthCredentials instance

    Returns:
        dict: Response from GHL API
    """
    url = "https://services.leadconnectorhq.com/invoices/"
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Content-Type": "application/json",
        "Version": "2021-07-28"
    }

    contact = Contact.objects.using('external').filter(contact_id=contact_id).first()

    if not contact:
        return {"error": "Contact not found"}
    
    line_items = []

    for service in services:
        product_name = service.get("name", "Unnamed Service")
        print("Processing service:", product_name)  # DEBUG

        product_info = get_or_create_product(
            credentials.access_token,
            credentials.location_id,
            product_name,
            custom_data=service
        )
        if not product_info:
            print(f"Skipping service: {product_name} (no product info)")
            continue  # <-- change return to continue, so other services are still added

        line_item = {
            "name": product_name,
            "description": service.get("description", ""),
            "currency": "USD",
            "qty": service.get("quantity", 1),
            "amount": service.get("price", 0.0),
            "productId": product_info["productId"],
        }

        if service.get("price", 0.0) > 0:
            line_item["taxes"] = [
                {
                    "_id": "sales-tax-8-25",
                    "name": "Sales Tax",
                    "rate": 8.25,
                    "calculation": "exclusive",
                    "description": "8.25% standard US sales tax"
                }
            ]

        line_items.append(line_item)

    print("Final line_items payload:", line_items)  # DEBUG

    discount= {
        "value":0,
        "type":'fixed' #percentage, fixed
    }

    contactDetails = {
        "id":contact_id,
        "name": contactName,
        "email": contact.email,
        "address":{"addressLine1":customer_address},
        "companyName": companyName,
        "phoneNo": phoneNo
    }

    businessDetails = {
        "logoUrl":'https://storage.googleapis.com/msgsndr/b8qvo7VooP3JD3dIZU42/media/683efc8fd5817643ff8194f0.jpeg',
        "name":"TruShine Window Cleaning",
    }

    sentTo = {
        "email":[contact.email]
    }

    issue_date = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")

    payload = {
        "altId": credentials.location_id,
        "altType":'location',
        "name": name,
        "businessDetails":businessDetails,
        "currency":"USD",
        "items": line_items,
        "discount":discount,
        "contactDetails":contactDetails,
        "issueDate":issue_date,
        "sentTo": sentTo,
        "liveMode":True,
        "tipsConfiguration":{
            "tipsEnabled": False,
            "tipsPercentage": []
        }
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.json()

    

def send_invoice(invoiceId):
    url = f'https://services.leadconnectorhq.com/invoices/{invoiceId}/send'
    credentials = GHLAuthCredentials.objects.first()
    
    headers = {
        'Authorization': f'Bearer {credentials.access_token}',
        'Version': '2021-07-28'
    }

    payload = {
        "altId": credentials.location_id,
        "altType":'location',
        "userId": credentials.user_id,
        "action":'email',
        "liveMode":True,
    }

    try:
        response = requests.post(url=url, headers=headers, json=payload)
        print('invoice_response', response.json())
        return response.json()
    except Exception as e:
        return {"error": str(e)}