from django.core.management.base import BaseCommand

from referral_app.tasks import backfill_referral_links


class Command(BaseCommand):
    help = "Generate referral links for every existing contact that does not have one."

    def add_arguments(self, parser):
        parser.add_argument(
            "--location-id",
            default="",
            help="Limit backfill to a single subaccount location_id.",
        )

    def handle(self, *args, **options):
        result = backfill_referral_links(location_id=options["location_id"])
        self.stdout.write(self.style.SUCCESS(f"Backfill complete: {result}"))
