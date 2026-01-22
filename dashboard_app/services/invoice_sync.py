import requests
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils.dateparse import parse_datetime, parse_date

from ..models import Invoice, InvoiceItem
from accounts.models import GHLAuthCredentials


class InvoiceSyncService:
    BASE_URL = "https://services.leadconnectorhq.com/invoices/"

    def __init__(self, location_id):
        self.location_id = location_id
        self.credentials = self._get_credentials()

    # ----------------------------
    # Auth & Headers
    # ----------------------------
    def _get_credentials(self):
        try:
            return GHLAuthCredentials.objects.get(location_id=self.location_id)
        except GHLAuthCredentials.DoesNotExist:
            raise ValueError(f"No credentials found for location: {self.location_id}")

    def _get_headers(self):
        return {
            "Accept": "application/json",
            "Version": "2021-07-28",
            "Authorization": f"Bearer {self.credentials.access_token}",
        }

    def _refresh_token_if_needed(self):
        """
        Optional: If your GHLAuthCredentials stores expiry / refresh token,
        implement logic to refresh the access token here and update credentials.
        For now this is a safe no-op — but a hook is provided.
        """
        # Example (pseudo):
        # if self.credentials.is_expired():
        #     new_tokens = refresh_with_refresh_token(self.credentials.refresh_token)
        #     self.credentials.access_token = new_tokens["access_token"]
        #     self.credentials.save()
        return

    # ----------------------------
    # Helpers
    # ----------------------------
    def _safe_get(self, data, key, default=None):
        if isinstance(data, dict):
            return data.get(key, default)
        return default

    def _parse_decimal(self, value, default=Decimal('0.00')):
        if value is None:
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return default

    def _parse_maybe_datetime(self, s):
        """
        The API sometimes returns full ISO datetimes, sometimes date-only strings.
        Try parse_datetime first, then parse_date (and convert to midnight UTC),
        otherwise return None.
        """
        if not s:
            return None
        dt = parse_datetime(s)
        if dt:
            return dt
        d = parse_date(s)
        if d:
            # convert date to datetime at midnight (naive). Let Django interpret timezone as needed.
            return datetime.combine(d, datetime.min.time())
        return None

    # ----------------------------
    # API Fetching
    # ----------------------------
    def fetch_invoice_by_id(self, invoice_id):
        url = f"{self.BASE_URL}{invoice_id}"
        params = {"altId": self.location_id, "altType": "location"}

        try:
            self._refresh_token_if_needed()
            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()
            data = response.json()
            # some endpoints return {'invoice': {...}} others return invoice obj directly
            return data.get("invoice", data)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching invoice {invoice_id}: {str(e)}")
            return None

    def fetch_all_invoices(self, limit=100):
        """
        Fetches invoices with pagination. Returns list of invoice objects.
        """
        all_invoices = []
        offset = 0
        while True:
            params = {
                "altId": self.location_id,
                "altType": "location",
                "limit": limit,
                "offset": offset,
            }
            try:
                self._refresh_token_if_needed()
                response = requests.get(self.BASE_URL, headers=self._get_headers(), params=params)
                response.raise_for_status()
                data = response.json()
                invoices = data.get("invoices", []) or []
                if not invoices:
                    break
                all_invoices.extend(invoices)
                total = data.get("total")
                if total is not None and len(all_invoices) >= int(total):
                    break
                offset += limit
            except requests.exceptions.RequestException as e:
                print(f"Error fetching invoices at offset {offset}: {str(e)}")
                break

        return all_invoices

    # ----------------------------
    # Parsing
    # ----------------------------
    def _parse_invoice_data(self, invoice_data):
        """
        Map API invoice_data to model-friendly dict.
        """
        business = self._safe_get(invoice_data, "businessDetails", {}) or {}
        contact = self._safe_get(invoice_data, "contactDetails", {}) or {}
        discount = self._safe_get(invoice_data, "discount", {}) or {}
        total_summary = self._safe_get(invoice_data, "totalSummary", {}) or {}
        currency_options = self._safe_get(invoice_data, "currencyOptions", {}) or {}
        sent_to = self._safe_get(invoice_data, "sentTo", {}) or {}
        sent_from = self._safe_get(invoice_data, "sentFrom", {}) or {}
        tips_config = self._safe_get(invoice_data, "tipsConfiguration", {}) or {}
        late_fees_config = self._safe_get(invoice_data, "lateFeesConfiguration", {}) or {}
        reminders_config = self._safe_get(invoice_data, "remindersConfiguration", {}) or {}

        parsed = {
            "invoice_id": invoice_data.get("_id"),
            "invoice_number": str(invoice_data.get("invoiceNumber")) if invoice_data.get("invoiceNumber") is not None else None,
            "alt_id": invoice_data.get("altId"),
            "alt_type": invoice_data.get("altType"),
            "company_id": invoice_data.get("companyId"),
            "location_id": self.location_id,
            "name": invoice_data.get("name", ""),
            "title": invoice_data.get("title", "INVOICE"),
            "status": invoice_data.get("status", "draft"),
            "live_mode": invoice_data.get("liveMode", True),
            "business_name": business.get("name"),
            "business_logo_url": business.get("logoUrl"),
            "business_address": business.get("address", {}),
            "business_phone": business.get("phoneNo"),
            "business_website": business.get("website"),
            "contact_id": contact.get("id") or contact.get("_id") or "",
            "contact_name": contact.get("name"),
            "contact_email": contact.get("email"),
            "contact_phone": contact.get("phoneNo"),
            "contact_company_name": contact.get("companyName"),
            "contact_address": contact.get("address", {}),
            "currency": invoice_data.get("currency", "USD"),
            "currency_symbol": currency_options.get("symbol", "$"),
            "sub_total": self._parse_decimal(total_summary.get("subTotal") or invoice_data.get("subTotal") or 0),
            "discount_value": self._parse_decimal(discount.get("value") or total_summary.get("discount") or invoice_data.get("discount") or 0),
            "discount_type": discount.get("type", "fixed"),
            "total": self._parse_decimal(invoice_data.get("total") or 0),
            "invoice_total": self._parse_decimal(invoice_data.get("invoiceTotal") or invoice_data.get("invoice_total") or invoice_data.get("total") or 0),
            "amount_paid": self._parse_decimal(invoice_data.get("amountPaid") or 0),
            "amount_due": self._parse_decimal(invoice_data.get("amountDue") or 0),
            "tax_total": self._parse_decimal(total_summary.get("tax") or 0),
            "issue_date": self._parse_maybe_datetime(invoice_data.get("issueDate")),
            "due_date": self._parse_maybe_datetime(invoice_data.get("dueDate")),
            "sent_at": self._parse_maybe_datetime(invoice_data.get("sentAt")) if invoice_data.get("sentAt") else None,
            "created_at": self._parse_maybe_datetime(invoice_data.get("createdAt")),
            "updated_at": self._parse_maybe_datetime(invoice_data.get("updatedAt")),
            "sent_to_emails": sent_to.get("email", []) or [],
            "sent_to_phones": sent_to.get("phoneNo", []) or [],
            "sent_from_name": sent_from.get("fromName"),
            "sent_from_email": sent_from.get("fromEmail"),
            "sent_by": invoice_data.get("sentBy"),
            "updated_by": invoice_data.get("updatedBy"),
            "terms_notes": invoice_data.get("termsNotes", ""),
            "attachments": invoice_data.get("attachments", []) or [],
            "opportunity_details": invoice_data.get("opportunityDetails"),
            "tips_enabled": tips_config.get("tipsEnabled", False),
            "tips_received": invoice_data.get("tipsReceived", []) or [],
            "late_fees_enabled": late_fees_config.get("enable", False),
            "late_fees_configuration": late_fees_config,
            "reminders_configuration": reminders_config,
            "reminders": invoice_data.get("reminders", []) or invoice_data.get("remindersConfiguration", {}).get("reminders", []) or [],
            "automatic_taxes_enabled": invoice_data.get("automaticTaxesEnabled", False),
            "automatic_taxes_calculated": invoice_data.get("automaticTaxesCalculated", False),
            "payment_schedule": invoice_data.get("paymentSchedule", {}) or {},
            "total_summary": total_summary or {},
        }

        return parsed

    # ----------------------------
    # Save Logic with Bulk Actions
    # ----------------------------
    @transaction.atomic
    def save_invoice(self, invoice_data):
        parsed = self._parse_invoice_data(invoice_data)
        items_data = invoice_data.get("invoiceItems", []) or []

        # update_or_create invoice
        invoice_obj, created = Invoice.objects.update_or_create(
            invoice_id=parsed["invoice_id"],
            defaults=parsed,
        )

        # remove existing items for this invoice and recreate (keeps it simple & consistent)
        InvoiceItem.objects.filter(invoice=invoice_obj).delete()

        valid_items = []
        for item in items_data:
            item_id = item.get("_id")
            if not item_id:
                # skip items without id
                continue

            name = item.get("name") or item.get("title") or ""
            qty = item.get("qty", 1)
            amount = item.get("amount", 0)

            valid_items.append(
                InvoiceItem(
                    invoice=invoice_obj,
                    item_id=item_id,
                    product_id=item.get("productId") or item.get("product_id"),
                    price_id=item.get("priceId") or item.get("price_id"),
                    name=name,
                    description=item.get("description", ""),
                    currency=item.get("currency", "USD"),
                    qty=self._parse_decimal(qty, default=Decimal('1.00')),
                    amount=self._parse_decimal(amount),
                    tax_inclusive=item.get("taxInclusive", False),
                    taxes=item.get("taxes", []) or [],
                )
            )

        if valid_items:
            InvoiceItem.objects.bulk_create(valid_items, ignore_conflicts=True)

        return invoice_obj, created

    # ----------------------------
    # Sync Handlers
    # ----------------------------
    def sync_invoice(self, invoice_id):
        if not invoice_id:
            raise ValueError("invoice_id must be provided for sync_invoice()")

        data = self.fetch_invoice_by_id(invoice_id)
        if not data:
            print(f"❌ [INVOICE SYNC] Failed to fetch invoice {invoice_id} from GHL API")
            return None

        invoice, created = self.save_invoice(data)
        action = 'created' if created else 'updated'
        print(f"✅ [INVOICE SYNC] Invoice {invoice.invoice_number or invoice.invoice_id} {action} successfully")
        print(f"   Status: {invoice.status}, Amount Due: ${invoice.amount_due}, Due Date: {invoice.due_date}")
        return invoice

    def sync_all_invoices(self):
        invoices = self.fetch_all_invoices()
        synced, created_count, updated_count = 0, 0, 0

        for data in invoices:
            try:
                invoice, created = self.save_invoice(data)
                synced += 1
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            except Exception as e:
                print(f"Error saving invoice {data.get('invoiceNumber') or data.get('_id')}: {str(e)}")

        print(f"Sync completed: {synced} total, {created_count} created, {updated_count} updated")
        return {
            "total": synced,
            "created": created_count,
            "updated": updated_count,
        }
    


    

    def bulk_sync_invoices(self):
        invoices_data = self.fetch_all_invoices()
        if not invoices_data:
            print("No invoices found from API.")
            return {"total": 0, "created": 0, "updated": 0, "deleted": 0}

        parsed_invoices = []
        all_items = []

        # Parse all invoices + collect items
        for data in invoices_data:
            parsed = self._parse_invoice_data(data)
            parsed_invoices.append(parsed)

            items = data.get("invoiceItems", []) or []
            for item in items:
                if not item.get("_id"):
                    continue
                all_items.append((parsed["invoice_id"], item))

        # Invoice IDs coming from GHL
        ghl_invoice_ids = [p["invoice_id"] for p in parsed_invoices]

        # Get existing invoices
        existing_qs = Invoice.objects.filter(invoice_id__in=ghl_invoice_ids)
        existing_map = {inv.invoice_id: inv for inv in existing_qs}

        new_objs = []
        update_objs = []

        # Create or update
        for parsed in parsed_invoices:
            existing = existing_map.get(parsed["invoice_id"])
            if existing:
                for field, value in parsed.items():
                    setattr(existing, field, value)
                update_objs.append(existing)
            else:
                new_objs.append(Invoice(**parsed))

        deleted_count = 0

        with transaction.atomic():
            # CREATE
            if new_objs:
                Invoice.objects.bulk_create(new_objs, ignore_conflicts=True)

            # RE-FETCH to get PKs
            all_invoices = Invoice.objects.filter(invoice_id__in=ghl_invoice_ids)
            invoice_pk_map = {inv.invoice_id: inv.pk for inv in all_invoices}

            # UPDATE
            if update_objs:
                fields = [
                    f.name for f in Invoice._meta.fields
                    if f.name not in ("id", "invoice_id", "created_at")
                ]
                Invoice.objects.bulk_update(update_objs, fields=fields)

            # DELETE ITEMS FOR ALL SYNCED INVOICES
            InvoiceItem.objects.filter(invoice__invoice_id__in=ghl_invoice_ids).delete()

            # ADD ITEMS
            valid_items = []
            for invoice_str_id, item in all_items:
                invoice_pk = invoice_pk_map.get(invoice_str_id)
                if not invoice_pk:
                    continue

                name = item.get("name") or item.get("title") or ""
                qty = item.get("qty", 1)
                amount = item.get("amount", 0)

                valid_items.append(
                    InvoiceItem(
                        invoice_id=invoice_pk,
                        item_id=item.get("_id"),
                        product_id=item.get("productId") or item.get("product_id"),
                        price_id=item.get("priceId") or item.get("price_id"),
                        name=name,
                        description=item.get("description", ""),
                        currency=item.get("currency", "USD"),
                        qty=self._parse_decimal(qty, default=Decimal('1.00')),
                        amount=self._parse_decimal(amount),
                        tax_inclusive=item.get("taxInclusive", False),
                        taxes=item.get("taxes", []) or [],
                    )
                )

            if valid_items:
                InvoiceItem.objects.bulk_create(valid_items, ignore_conflicts=True)

            # -----------------------------------------
            # DELETE INVOICES NOT IN GHL ANYMORE
            # -----------------------------------------
            to_delete_qs = Invoice.objects.exclude(invoice_id__in=ghl_invoice_ids)
            deleted_count = to_delete_qs.count()
            to_delete_qs.delete()

        print(
            f"Sync completed: {len(parsed_invoices)} total, "
            f"{len(new_objs)} created, {len(update_objs)} updated, {deleted_count} deleted"
        )

        return {
            "total": len(parsed_invoices),
            "created": len(new_objs),
            "updated": len(update_objs),
            "deleted": deleted_count,
        }





# ----------------------------
# Public Function Entry Point
# ----------------------------
def sync_invoices(location_id, invoice_id=None):
    service = InvoiceSyncService(location_id)
    if invoice_id:
        return service.sync_invoice(invoice_id)
    else:
        return service.bulk_sync_invoices()
