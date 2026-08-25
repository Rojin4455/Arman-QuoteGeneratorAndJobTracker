from django.core.management.base import BaseCommand

from referral_app.ghl_sync import ensure_referral_link_custom_field_for_all_accounts


class Command(BaseCommand):
    help = (
        "Create GHL contact custom fields Referral Link, Referral Reward, "
        "Friend Discount, and Referral Minimum on every active onboarded "
        "subaccount (skips fields that already exist)."
    )

    def handle(self, *args, **options):
        summary = ensure_referral_link_custom_field_for_all_accounts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Referral GHL fields: ok={len(summary['ok'])} "
                f"failed={len(summary['failed'])} skipped={len(summary['skipped'])}"
            )
        )
        if summary["failed"]:
            self.stdout.write(self.style.ERROR(f"Failed locations: {summary['failed']}"))
        if summary["ok"]:
            self.stdout.write(f"Ready: {summary['ok']}")
