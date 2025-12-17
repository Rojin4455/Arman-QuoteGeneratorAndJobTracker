from decouple import config
import requests
from django.http import JsonResponse
import json
from django.shortcuts import redirect
from accounts.models import GHLAuthCredentials,Webhook
from django.views.decorators.csrf import csrf_exempt
import logging
from django.views import View
from django.utils.decorators import method_decorator
import traceback
from accounts.tasks import fetch_all_contacts_task,handle_webhook_event
from accounts.utils import sync_all_users_to_db
from dashboard_app.tasks import sync_single_invoice_task,delete_invoice_task





logger = logging.getLogger(__name__)


GHL_CLIENT_ID = config("GHL_CLIENT_ID")
GHL_CLIENT_SECRET = config("GHL_CLIENT_SECRET")
GHL_REDIRECTED_URI = config("GHL_REDIRECTED_URI")
TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
SCOPE = config("SCOPE")

def auth_connect(request):
    auth_url = ("https://marketplace.gohighlevel.com/oauth/chooselocation?response_type=code&"
                f"redirect_uri={GHL_REDIRECTED_URI}&"
                f"client_id={GHL_CLIENT_ID}&"
                f"scope={SCOPE}"
                )
    return redirect(auth_url)



def callback(request):
    
    code = request.GET.get('code')

    if not code:
        return JsonResponse({"error": "Authorization code not received from OAuth"}, status=400)

    return redirect(f'{config("BASE_URI")}/api/accounts/auth/tokens?code={code}')


def tokens(request):
    authorization_code = request.GET.get("code")

    if not authorization_code:
        return JsonResponse({"error": "Authorization code not found"}, status=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": GHL_CLIENT_ID,
        "client_secret": GHL_CLIENT_SECRET,
        "redirect_uri": GHL_REDIRECTED_URI,
        "code": authorization_code,
    }

    response = requests.post(TOKEN_URL, data=data)

    try:
        response_data = response.json()
        if not response_data:
            return

        obj, created = GHLAuthCredentials.objects.update_or_create(
            location_id= response_data.get("locationId"),
            defaults={
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "expires_in": response_data.get("expires_in"),
                "scope": response_data.get("scope"),
                "user_type": response_data.get("userType"),
                "company_id": response_data.get("companyId"),
                "user_id":response_data.get("userId"),

            }
        )
        # fetch_all_contacts_task.delay(response_data.get("locationId"), response_data.get("access_token"))
        return JsonResponse({
            "message": "Authentication successful",
            "access_token": response_data.get('access_token'),
            "token_stored": True
        })
        
    except requests.exceptions.JSONDecodeError:
        return JsonResponse({
            "error": "Invalid JSON response from API",
            "status_code": response.status_code,
            "response_text": response.text[:500]
        }, status=500)
    

def sync_all_contacts_and_address(request):

    try:
        
        obj = GHLAuthCredentials.objects.first()
        fetch_all_contacts_task.delay(obj.location_id, obj.access_token)
        return JsonResponse({
            "message": "Authentication successful",
            "access_token": obj.access_token,
            "token_stored": True
        })
        
    except requests.exceptions.JSONDecodeError:
        return JsonResponse({
            "error": "Invalid JSON response from API",
        }, status=500)
    

@csrf_exempt
def webhook_handler(request):
    if request.method != "POST":
        return JsonResponse({"message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        print("date:----- ", data)

        # Create Webhook record
        Webhook.objects.create(
            event=data.get("type", "unknown"),
            company_id=data.get("locationId", "unknown"),
            payload=data
        )

        # Dispatch async handler
        event_type = data.get("type")
        handle_webhook_event.delay(data, event_type)

        invoice_events = ["InvoiceCreate", "InvoiceUpdate", "InvoiceDelete"]
        if event_type in invoice_events:
            location_id = data.get("locationId")
            # Extract invoice_id from various possible locations in the payload
            invoice_id = None
            invoice_obj = data.get("invoice")
            if isinstance(invoice_obj, dict):
                invoice_id = invoice_obj.get("_id") or invoice_obj.get("id")
            if not invoice_id:
                invoice_id = data.get("invoiceId") or data.get("_id")
            
            if location_id and invoice_id:
                if event_type in ["InvoiceCreate", "InvoiceUpdate"]:
                    # Sync invoice for create and update events
                    sync_single_invoice_task.delay(location_id, invoice_id)
                    print(f"Triggered invoice sync for {event_type}: invoice_id={invoice_id}, location_id={location_id}")
                elif event_type == "InvoiceDelete":
                    # Delete invoice for delete event
                    delete_invoice_task.delay(invoice_id)
                    print(f"Triggered invoice deletion for {event_type}: invoice_id={invoice_id}")
            else:
                print(f"Missing location_id or invoice_id in webhook payload for {event_type}")

        return JsonResponse({"message": "Webhook received"}, status=200)

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)


def sync_all_users(request):
    """
    Manually sync all users from GHL API to local database.
    Fetches all users for the authenticated location and creates/updates them.
    """
    try:
        # Get the first available GHL credentials
        credentials = GHLAuthCredentials.objects.first()
        
        if not credentials:
            return JsonResponse({
                "error": "No GHL credentials found. Please authenticate first."
            }, status=400)
        
        if not credentials.location_id or not credentials.access_token:
            return JsonResponse({
                "error": "Invalid GHL credentials. Missing location_id or access_token."
            }, status=400)
        
        # Sync all users
        result = sync_all_users_to_db(credentials.location_id, credentials.access_token)
        
        return JsonResponse({
            "message": "Users synced successfully",
            "created": result["created"],
            "updated": result["updated"],
            "total": result["total"]
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error syncing users: {str(e)}")
        return JsonResponse({
            "error": f"Failed to sync users: {str(e)}"
        }, status=500)

    