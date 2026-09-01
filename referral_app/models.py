import uuid

from django.db import models


DEFAULT_TERMS = (
    "Credits are issued after the referred customer completes and pays for an eligible "
    "first invoice. Existing customers, duplicates, and self-referrals do not qualify. "
    "Credits have no cash value, cannot be transferred, and are subject to the monthly "
    "referrer cap. Credits auto-apply to a future invoice."
)


class ReferralProgram(models.Model):
    REWARD_TWO_SIDED = "two_sided"
    REWARD_REFERRER_ONLY = "referrer_only"
    REWARD_FRIEND_ONLY = "friend_only"
    REWARD_MODE_CHOICES = [
        (REWARD_TWO_SIDED, "Both people"),
        (REWARD_REFERRER_ONLY, "Referrer only"),
        (REWARD_FRIEND_ONLY, "New customer only"),
    ]

    INVITE_COMPLETED_JOB = "completed_job"
    INVITE_REVIEW_CLICKED = "review_clicked"
    INVITE_FIVE_STAR = "five_star_review"
    INVITE_EITHER = "either"
    INVITATION_TRIGGER_CHOICES = [
        (INVITE_COMPLETED_JOB, "Job completed"),
        (INVITE_REVIEW_CLICKED, "Review clicked"),
        (INVITE_FIVE_STAR, "Five-star review"),
        (INVITE_EITHER, "Job completed or five-star review"),
    ]

    CADENCE_ONCE = "once"
    CADENCE_QUARTERLY = "quarterly"
    CADENCE_SEMIANNUAL = "semiannual"
    CADENCE_ANNUAL = "annual"
    INVITE_CADENCE_CHOICES = [
        (CADENCE_ONCE, "One time only"),
        (CADENCE_QUARTERLY, "Every 3 months"),
        (CADENCE_SEMIANNUAL, "Every 6 months"),
        (CADENCE_ANNUAL, "Every year"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="referral_program",
    )
    enabled = models.BooleanField(default=True)
    reward_mode = models.CharField(
        max_length=20,
        choices=REWARD_MODE_CHOICES,
        default=REWARD_TWO_SIDED,
    )
    referrer_reward_cents = models.PositiveIntegerField(default=2500)
    friend_reward_cents = models.PositiveIntegerField(default=2500)
    minimum_invoice_cents = models.PositiveIntegerField(default=10000)
    monthly_referrer_cap_cents = models.PositiveIntegerField(default=25000)
    invitation_trigger = models.CharField(
        max_length=32,
        choices=INVITATION_TRIGGER_CHOICES,
        default=INVITE_COMPLETED_JOB,
    )
    auto_invite_enabled = models.BooleanField(default=True)
    email_invite_enabled = models.BooleanField(default=True)
    sms_invite_enabled = models.BooleanField(default=True)
    email_delay_minutes = models.PositiveIntegerField(default=60)
    sms_delay_minutes = models.PositiveIntegerField(default=5)
    email_cadence = models.CharField(
        max_length=20,
        choices=INVITE_CADENCE_CHOICES,
        default=CADENCE_ONCE,
    )
    sms_cadence = models.CharField(
        max_length=20,
        choices=INVITE_CADENCE_CHOICES,
        default=CADENCE_ONCE,
    )
    primary_color = models.CharField(max_length=16, default="#1472e8")
    accent_color = models.CharField(max_length=16, default="#0c4fac")
    service_label = models.CharField(max_length=80, default="home service")
    terms_text = models.TextField(default=DEFAULT_TERMS)
    gift_card_purchase_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="Customer-facing gift card purchase page URL for this subaccount.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referral_programs"

    def __str__(self):
        return f"ReferralProgram({self.account_id})"


class ReferralLink(models.Model):
    """Reusable personal referral code for an existing customer (Contact)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="referral_links",
    )
    contact = models.ForeignKey(
        "accounts.Contact",
        on_delete=models.CASCADE,
        related_name="referral_links",
    )
    code = models.CharField(max_length=32, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referral_links"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "contact"],
                name="uniq_referral_link_account_contact",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "code"]),
        ]

    def __str__(self):
        return self.code


class ReferralAttribution(models.Model):
    STATUS_PENDING = "pending"
    STATUS_QUALIFIED = "qualified"
    STATUS_REJECTED = "rejected"
    STATUS_REVERSED = "reversed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_QUALIFIED, "Qualified"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_REVERSED, "Reversed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="referral_attributions",
    )
    referrer_contact = models.ForeignKey(
        "accounts.Contact",
        on_delete=models.CASCADE,
        related_name="referrals_made",
    )
    referred_contact = models.ForeignKey(
        "accounts.Contact",
        on_delete=models.CASCADE,
        related_name="referral_attributions",
    )
    referral_code = models.CharField(max_length=32)
    referred_email = models.EmailField()
    referred_phone = models.CharField(max_length=30, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    source = models.CharField(max_length=32, default="share_link")
    quote_id = models.UUIDField(null=True, blank=True, db_index=True)
    qualifying_job_id = models.UUIDField(null=True, blank=True)
    qualifying_invoice_id = models.CharField(max_length=100, blank=True, default="")
    qualifying_invoice_cents = models.PositiveIntegerField(null=True, blank=True)
    qualified_at = models.DateTimeField(null=True, blank=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    # Friend-side upfront discount (snapshot at claim time; applied on first job)
    friend_discount_cents = models.PositiveIntegerField(default=0)
    discount_job_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Job currently carrying the friend referral discount.",
    )
    discount_applied_cents = models.PositiveIntegerField(
        default=0,
        help_text="Referral discount actually applied on the qualifying invoice.",
    )
    discount_disabled = models.BooleanField(
        default=False,
        help_text="Admin manually disabled the referral discount for the qualifying job.",
    )
    discount_disabled_by = models.CharField(max_length=150, blank=True, default="")

    # Referrer-side reward accounting (credited only after invoice fully paid)
    reward_credited_cents = models.PositiveIntegerField(default=0)
    reward_credited_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referral_attributions"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "referred_contact"],
                name="uniq_referral_account_referred_contact",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "status", "created_at"]),
            models.Index(fields=["referrer_contact", "status"]),
            models.Index(fields=["account", "referred_email"]),
        ]

    def __str__(self):
        return f"{self.referral_code} → {self.referred_email} ({self.status})"


class CustomerCreditLedger(models.Model):
    TYPE_REFERRER_REWARD = "referrer_reward"
    TYPE_FRIEND_REWARD = "friend_reward"
    TYPE_APPLICATION = "application"
    TYPE_RELEASE = "release"
    TYPE_REVERSAL = "reversal"
    TYPE_MANUAL = "manual_adjustment"
    ENTRY_TYPE_CHOICES = [
        (TYPE_REFERRER_REWARD, "Referrer reward"),
        (TYPE_FRIEND_REWARD, "Friend reward"),
        (TYPE_APPLICATION, "Invoice application"),
        (TYPE_RELEASE, "Released reservation"),
        (TYPE_REVERSAL, "Reversal"),
        (TYPE_MANUAL, "Manual adjustment"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="customer_credit_ledger",
    )
    contact = models.ForeignKey(
        "accounts.Contact",
        on_delete=models.CASCADE,
        related_name="credit_ledger",
    )
    referral = models.ForeignKey(
        ReferralAttribution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    entry_type = models.CharField(max_length=32, choices=ENTRY_TYPE_CHOICES)
    amount_cents = models.IntegerField(help_text="Signed integer cents. Positive credits the customer.")
    balance_after_cents = models.IntegerField()
    invoice_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    job_id = models.UUIDField(null=True, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customer_credit_ledger"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "idempotency_key"],
                name="uniq_credit_ledger_account_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "contact", "created_at"]),
            models.Index(fields=["account", "created_at"]),
            models.Index(fields=["invoice_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.entry_type} {self.amount_cents}¢ for contact {self.contact_id}"


class ReferralProcessedEvent(models.Model):
    """Account-scoped idempotency for job/invoice/review events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        "accounts.GHLAuthCredentials",
        on_delete=models.CASCADE,
        related_name="referral_processed_events",
    )
    event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referral_processed_events"
        constraints = [
            models.UniqueConstraint(
                fields=["account", "event_id"],
                name="uniq_referral_processed_event_account_event",
            ),
        ]

    def __str__(self):
        return f"{self.event_type}:{self.event_id}"
