from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q

from accounts.models import Contact, GHLAuthCredentials
from accounts.utils import create_or_update_contact
from jobtracker_app.ghl_contact_link import diagnose_job_contact, job_contact_candidates
from jobtracker_app.models import Job


class Command(BaseCommand):
    help = (
        "Check that open jobs (and completed jobs without an invoice) point at a contact that "
        "exists in GHL. Missing ones are searched in GHL by email, then phone. "
        "Dry run by default; pass --apply to relink the jobs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Relink jobs to the GHL contact found.")
        parser.add_argument("--location", help="Only this GHL location id.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        uninvoiced = (Q(invoice_id__isnull=True) | Q(invoice_id="")) & (
            Q(invoice_url__isnull=True) | Q(invoice_url="")
        )
        qs = (
            Job.objects.filter(~Q(status__in=["completed", "cancelled"]) | (Q(status="completed") & uninvoiced))
            .exclude(account__isnull=True)
            .select_related("account", "contact", "submission__contact")
        )
        if options["location"]:
            qs = qs.filter(account__location_id=options["location"])

        groups = defaultdict(list)
        for job in qs:
            current_id, emails, phones = job_contact_candidates(job)
            groups[(job.account.location_id, current_id, tuple(emails), tuple(phones))].append(job)

        credentials = {c.location_id: c for c in GHLAuthCredentials.objects.all()}
        totals = Counter()
        rows = []
        for (location_id, current_id, emails, phones), jobs in groups.items():
            creds = credentials.get(location_id)
            if not creds or not creds.access_token:
                totals["unknown"] += 1
                continue
            result = diagnose_job_contact(jobs[0], creds)
            totals[result["status"]] += 1
            if result["status"] == "ok":
                continue

            action = ""
            if result["status"] == "relinkable":
                ghl = result["ghl_contact"]
                action = f"-> {ghl.get('id')} ({ghl.get('email') or ghl.get('phone')}) by {result['matched_by']}"
                if apply_changes:
                    local = create_or_update_contact({"contact": ghl, "locationId": location_id})
                    local = local or Contact.objects.filter(contact_id=ghl.get("id")).first()
                    if local:
                        updates = {"contact": local, "ghl_contact_id": local.contact_id}
                        if local.email:
                            updates["customer_email"] = local.email
                        count = Job.objects.filter(id__in=[j.id for j in jobs]).update(**updates)
                        action += f" | relinked {count} job(s)"
                    else:
                        action += " | could not save contact locally"

            statuses = Counter(j.status for j in jobs)
            dates = sorted(j.scheduled_at for j in jobs if j.scheduled_at)
            rows.append(
                (
                    result["status"],
                    jobs[0].customer_name or (jobs[0].contact and str(jobs[0].contact.first_name)) or "-",
                    ", ".join(emails) or "-",
                    ", ".join(phones) or "-",
                    current_id or "-",
                    result.get("reason", ""),
                    len(jobs),
                    dict(statuses),
                    dates[0].date() if dates else None,
                    action,
                )
            )

        order = {"missing": 0, "relinkable": 1, "unknown": 2}
        for row in sorted(rows, key=lambda r: (order.get(r[0], 9), str(r[8]))):
            status, name, emails, phones, current_id, reason, count, statuses, first, action = row
            self.stdout.write(
                f"[{status.upper()}] {name} | email={emails} | phone={phones} | id={current_id} | "
                f"{reason} | jobs={count} {statuses} | first={first} {action}"
            )
        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: {len(groups)} customer group(s) | {dict(totals)}"))
