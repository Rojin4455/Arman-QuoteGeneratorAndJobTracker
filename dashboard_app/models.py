from decimal import Decimal
from django.db import models
from django.contrib.postgres.fields import ArrayField


class Invoice(models.Model):
    """Invoice synced from GHL. Scoped to one GHL account (GHLAuthCredentials)."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('payment_processing', 'Payment Processing'),
        ('paid', 'Paid'),
        ('partially_paid', 'Partially Paid'),
        ('partial', 'Partial'),  # kept for backward-compatibility if used elsewhere
        ('overdue', 'Overdue'),
        ('void', 'Void'),
    ]

    # Account (multi-account support)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='invoices',
        null=True,
        blank=True,
        help_text='GHL account this invoice belongs to (for multi-account onboarding)',
    )

    # Primary identifiers
    invoice_id = models.CharField(max_length=100, db_index=True)  # maps to _id; unique per account
    invoice_number = models.CharField(max_length=50, db_index=True, blank=True, null=True)
    alt_id = models.CharField(max_length=100, db_index=True, blank=True, null=True)
    alt_type = models.CharField(max_length=50, blank=True, null=True)  # typically "location"
    company_id = models.CharField(max_length=100, db_index=True, blank=True, null=True)
    location_id = models.CharField(max_length=100, db_index=True, blank=True, null=True)

    # Basic invoice info
    name = models.CharField(max_length=500, blank=True, null=True)
    title = models.CharField(max_length=200, default='INVOICE', blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, db_index=True, default='draft')
    live_mode = models.BooleanField(default=True)

    # Business details
    business_name = models.CharField(max_length=300, blank=True, null=True)
    business_logo_url = models.URLField(max_length=500, blank=True, null=True)
    business_address = models.JSONField(default=dict, blank=True)  # maps businessDetails.address
    business_phone = models.CharField(max_length=50, blank=True, null=True)
    business_website = models.CharField(max_length=500, blank=True, null=True)

    # Contact details
    contact_id = models.CharField(max_length=100, db_index=True, blank=True, null=True)
    contact_name = models.CharField(max_length=300, blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=50, blank=True, null=True)
    contact_company_name = models.CharField(max_length=300, blank=True, null=True)
    contact_address = models.JSONField(default=dict, blank=True)

    # Financial details (use DecimalField)
    currency = models.CharField(max_length=10, default='USD', blank=True, null=True)
    currency_symbol = models.CharField(max_length=5, default='$', blank=True, null=True)
    sub_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    discount_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    discount_type = models.CharField(max_length=50, default='fixed', blank=True, null=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), db_index=True)
    invoice_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), db_index=True)
    amount_due = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), db_index=True)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # Dates (allow null to be safe with inconsistent API data)
    issue_date = models.DateTimeField(blank=True, null=True, db_index=True)
    due_date = models.DateTimeField(blank=True, null=True, db_index=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    # Sent details
    sent_to_emails = ArrayField(models.EmailField(), default=list, blank=True)
    sent_to_phones = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    sent_from_name = models.CharField(max_length=300, blank=True, null=True)
    sent_from_email = models.EmailField(blank=True, null=True)
    sent_by = models.CharField(max_length=100, blank=True, null=True)
    updated_by = models.CharField(max_length=100, blank=True, null=True)

    # Additional fields
    terms_notes = models.TextField(blank=True, null=True)
    attachments = models.JSONField(default=list, blank=True)
    opportunity_details = models.JSONField(default=dict, blank=True, null=True)

    # Tax / Payment / Reminder configs
    automatic_taxes_enabled = models.BooleanField(default=False)
    automatic_taxes_calculated = models.BooleanField(default=False)
    payment_schedule = models.JSONField(default=dict, blank=True)  # paymentSchedule object
    total_summary = models.JSONField(default=dict, blank=True)
    reminders_configuration = models.JSONField(default=dict, blank=True)

    tips_enabled = models.BooleanField(default=False)
    tips_received = models.JSONField(default=list, blank=True)

    late_fees_enabled = models.BooleanField(default=False)
    late_fees_configuration = models.JSONField(default=dict, blank=True)

    reminders_configuration = models.JSONField(default=dict, blank=True)
    reminders = models.JSONField(default=list, blank=True)  # array of reminder objects from schema

    # Sync metadata
    last_synced = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        ordering = ['-created_at']
        unique_together = [['account', 'invoice_id']]
        indexes = [
            models.Index(fields=['account', 'location_id']),
            models.Index(fields=['location_id', 'status']),
            models.Index(fields=['contact_id', 'status']),
            models.Index(fields=['due_date', 'status']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.invoice_number or self.invoice_id} - {self.contact_name or ''} - ${self.total}"


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    item_id = models.CharField(max_length=100, db_index=True)  # maps to _id for the item
    product_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    price_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)  # maps priceId

    name = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    currency = models.CharField(max_length=10, default='USD', blank=True, null=True)

    qty = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('1.00'))
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    tax_inclusive = models.BooleanField(default=False)
    taxes = models.JSONField(default=list, blank=True)  # array of tax objects

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invoice_items'
        ordering = ['id']
        indexes = [
            models.Index(fields=['item_id']),
            models.Index(fields=['product_id']),
        ]

    def __str__(self):
        return f"{self.name or self.item_id} - {self.qty} x ${self.amount}"


