from django.contrib import admin

from referral_app.models import (
    CustomerCreditLedger,
    ReferralAttribution,
    ReferralLink,
    ReferralProcessedEvent,
    ReferralProgram,
)


@admin.register(ReferralProgram)
class ReferralProgramAdmin(admin.ModelAdmin):
    list_display = ("account", "enabled", "reward_mode", "referrer_reward_cents", "friend_reward_cents", "updated_at")
    list_filter = ("enabled", "reward_mode")


@admin.register(ReferralLink)
class ReferralLinkAdmin(admin.ModelAdmin):
    list_display = ("code", "account", "contact", "created_at")
    search_fields = ("code", "contact__email", "contact__first_name", "contact__last_name")


@admin.register(ReferralAttribution)
class ReferralAttributionAdmin(admin.ModelAdmin):
    list_display = ("referral_code", "referred_email", "status", "account", "qualified_at", "created_at")
    list_filter = ("status",)
    search_fields = ("referral_code", "referred_email")


@admin.register(CustomerCreditLedger)
class CustomerCreditLedgerAdmin(admin.ModelAdmin):
    list_display = ("contact", "entry_type", "amount_cents", "balance_after_cents", "invoice_id", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("idempotency_key", "invoice_id", "description")


@admin.register(ReferralProcessedEvent)
class ReferralProcessedEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "event_id", "account", "created_at")
    search_fields = ("event_id",)
