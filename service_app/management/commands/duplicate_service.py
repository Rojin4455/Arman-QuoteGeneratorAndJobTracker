from django.core.management.base import BaseCommand, CommandError

from service_app.models import Service
from service_app.service_clone import duplicate_service


class Command(BaseCommand):
    help = (
        "Duplicate a service inside the same account with all features, packages, "
        "questions and pricing rules. Example: "
        "duplicate_service --location-id LOC --name 'Holiday Lighting' --new-name 'Front Holiday Lighting'"
    )

    def add_arguments(self, parser):
        parser.add_argument("--location-id", required=True, help="GHL location_id of the account")
        parser.add_argument("--name", required=True, help="Exact name of the service to copy")
        parser.add_argument("--new-name", required=True, help="Name for the copy")

    def handle(self, *args, **o):
        qs = Service.objects.filter(account__location_id=o["location_id"], name=o["name"])
        if qs.count() != 1:
            raise CommandError(f"Expected exactly 1 service named {o['name']!r}, found {qs.count()}.")
        new = duplicate_service(qs.get(), o["new_name"])
        self.stdout.write(self.style.SUCCESS(f"Created {new.id}  {new.name}"))
