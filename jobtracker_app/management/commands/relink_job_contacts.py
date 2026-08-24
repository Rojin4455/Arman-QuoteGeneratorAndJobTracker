from django.core.management.base import BaseCommand

from accounts.models import Contact
from jobtracker_app.models import Job


class Command(BaseCommand):
    help = (
        "Relink jobs whose contact FK is missing to the matching Contact "
        "(by ghl_contact_id, then customer_email). Fixes jobs whose contact "
        "link was cleared by the old edit bug."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only, no writes.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = Job.objects.filter(contact__isnull=True).exclude(account__isnull=True)
        relinked = 0
        unmatched = 0
        for job in qs.iterator(chunk_size=500):
            contact = None
            ghl_id = (job.ghl_contact_id or "").strip()
            if ghl_id:
                contact = Contact.objects.filter(
                    account=job.account, contact_id=ghl_id
                ).first()
            if not contact and (job.customer_email or "").strip():
                contact = (
                    Contact.objects.filter(
                        account=job.account, email__iexact=job.customer_email.strip()
                    )
                    .order_by("-id")
                    .first()
                )
            if not contact:
                unmatched += 1
                continue
            if not dry_run:
                Job.objects.filter(pk=job.pk).update(contact=contact)
            relinked += 1
            self.stdout.write(f"{'[dry-run] ' if dry_run else ''}Job {job.id} → contact {contact.id} ({contact.email or contact.contact_id})")

        self.stdout.write(self.style.SUCCESS(f"Relinked: {relinked}, no match: {unmatched}"))
