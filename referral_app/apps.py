from django.apps import AppConfig


class ReferralAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "referral_app"
    verbose_name = "Customer Referrals"

    def ready(self):
        from referral_app import signals  # noqa: F401
