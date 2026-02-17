"""
Management command to backfill the account (GHLAuthCredentials) on records that have null account.

Only updates records where the account field is currently null. Use when migrating to
multi-account: run once per location_id to set account on existing records for that location.

Usage:
    python manage.py backfill_account --location_id 2gQq7YvjmiZkoV21TvQU
    python manage.py backfill_account --location_id 2gQq7YvjmiZkoV21TvQU --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


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

    def handle(self, *args, **options):
        location_id = options["location_id"].strip()
        dry_run = options["dry_run"]

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

        # (model, field_name): field_name is the FK attr to GHLAuthCredentials (account or credentials)
        models_to_backfill = self._get_models_to_backfill()
        total_updated = 0

        # Bulk update: one SQL UPDATE per model (e.g. UPDATE table SET account_id=X WHERE account_id IS NULL)
        with transaction.atomic():
            for model, field_name in models_to_backfill:
                label = f"{model._meta.label}.{field_name}"
                qs = model.objects.filter(**{field_name: None})
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

    def _get_models_to_backfill(self):
        """Return list of (model, field_name) for models that have account/credentials FK to GHLAuthCredentials."""
        from accounts.models import (
            GHLAuthCredentials,
            GHLCustomField,
            GHLMediaStorage,
            Contact,
            Calendar,
            GHLLocationIndex,
        )
        from service_app.models import User, Location, GlobalBasePrice, Service, GlobalSizePackage, Appointment
        from quote_app.models import CustomerSubmission

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
        ]
