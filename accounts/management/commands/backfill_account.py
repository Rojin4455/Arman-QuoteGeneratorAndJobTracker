"""
Management command to backfill the account (GHLAuthCredentials) on records that have null account.

Only updates records where the account field is currently null. Use when migrating to
multi-account: run once per location_id to set account on existing records for that location.

Usage:
    python manage.py backfill_account --location_id 2gQq7YvjmiZkoV21TvQU
    python manage.py backfill_account --location_id 2gQq7YvjmiZkoV21TvQU --dry-run
    # Map ALL jobs with null account to this account (single-account or claim orphans):
    python manage.py backfill_account --location_id 2gQq7YvjmiZkoV21TvQU --job-claim-all-null
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q


class Command(BaseCommand):
    help = (
        "Backfill account (GHLAuthCredentials) on records with null account. "
        "Pass location_id to set account for that GHL location. Only null-account rows are updated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--location_id",
            type=str,
            required=True,
            help="GHL location_id; the account (GHLAuthCredentials) with this location_id will be used.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without writing to the database.",
        )
        parser.add_argument(
            "--job-claim-all-null",
            action="store_true",
            help="For Job: assign this account to ALL jobs with null account (not only those linked via submission/contact). Use when all jobs belong to this account or to claim orphan jobs.",
        )

    def handle(self, *args, **options):
        location_id = options["location_id"].strip()
        dry_run = options["dry_run"]
        job_claim_all_null = options.get("job_claim_all_null", False)

        if not location_id:
            raise CommandError("--location_id is required.")

        from accounts.models import GHLAuthCredentials

        account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if not account:
            raise CommandError(
                f"No GHLAuthCredentials found for location_id={location_id!r}. "
                "Create the account (e.g. via OAuth onboarding) first."
            )

        self.stdout.write(f"Using account: {account} (location_id={location_id})")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: no changes will be saved."))

        # (model, field_name) or (model, field_name, extra_filter): extra_filter limits which null-account rows to update
        models_to_backfill = self._get_models_to_backfill(account, location_id, job_claim_all_null=job_claim_all_null)
        total_updated = 0

        # Bulk update: one SQL UPDATE per model (e.g. UPDATE table SET account_id=X WHERE account_id IS NULL [and extra_filter])
        with transaction.atomic():
            for item in models_to_backfill:
                if len(item) == 3:
                    model, field_name, extra_filter = item
                else:
                    model, field_name = item
                    extra_filter = None
                label = f"{model._meta.label}.{field_name}"
                qs = model.objects.filter(**{field_name: None})
                if extra_filter is not None:
                    if isinstance(extra_filter, Q):
                        qs = qs.filter(extra_filter)
                    else:
                        qs = qs.filter(**extra_filter)
                if dry_run:
                    count = qs.count()
                    if count > 0:
                        self.stdout.write(f"  [dry-run] {label}: would update {count} row(s)")
                else:
                    updated = qs.update(**{field_name: account})
                    if updated > 0:
                        total_updated += updated
                        self.stdout.write(self.style.SUCCESS(f"  {label}: updated {updated} row(s)"))
            if dry_run:
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run complete. No changes saved."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Backfill complete. Total rows updated: {total_updated}"))

    def _get_models_to_backfill(self, account, location_id, job_claim_all_null=False):
        """
        Return list of (model, field_name) or (model, field_name, extra_filter).
        extra_filter: optional dict or Q to restrict which null-account rows are updated.
        job_claim_all_null: if True, Job backfill updates ALL jobs with null account (no submission/contact filter).
        """
        from accounts.models import (
            GHLCustomField,
            GHLMediaStorage,
            Contact,
            Calendar,
            GHLLocationIndex,
        )
        from service_app.models import User, Location, GlobalBasePrice, Service, GlobalSizePackage, Appointment
        from quote_app.models import CustomerSubmission
        from dashboard_app.models import Invoice
        from payroll_app.models import EmployeeProfile, PayrollSettings
        from jobtracker_app.models import Job

        return [
            # accounts
            (GHLCustomField, "account"),
            (GHLMediaStorage, "credentials"),  # FK name is credentials
            (Contact, "account"),
            (Calendar, "account"),
            (GHLLocationIndex, "account"),
            # service_app
            (User, "account"),
            (Location, "account"),
            (GlobalBasePrice, "account"),
            (Service, "account"),
            (GlobalSizePackage, "account"),
            (Appointment, "account"),
            # quote_app
            (CustomerSubmission, "account"),
            # dashboard_app: only invoices for this location_id
            (Invoice, "account", {"location_id": location_id}),
            # payroll_app: only profiles whose user belongs to this account; settings for this account
            (EmployeeProfile, "account", {"user__account_id": account.pk}),
            (PayrollSettings, "account"),
            # jobtracker_app: with --job-claim-all-null update ALL null-account jobs; else only those linked via submission/contact
            (Job, "account", None if job_claim_all_null else (Q(submission__account_id=account.pk) | Q(contact__account_id=account.pk))),
        ]
