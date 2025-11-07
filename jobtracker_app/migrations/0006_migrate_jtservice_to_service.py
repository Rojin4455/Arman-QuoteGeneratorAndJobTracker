# Generated migration to migrate JTService data to Service before changing foreign key

from django.db import migrations
from decimal import Decimal


def migrate_jtservice_to_service(apps, schema_editor):
    """
    Migrate JTService records to Service records and update JobServiceItem references.
    For each JTService:
    1. Try to find a matching Service by name
    2. If not found, create a new Service with the JTService data
    3. Update all JobServiceItem records that reference this JTService to reference the Service instead
    """
    JTService = apps.get_model('jobtracker_app', 'JTService')
    Service = apps.get_model('service_app', 'Service')
    JobServiceItem = apps.get_model('jobtracker_app', 'JobServiceItem')
    db_alias = schema_editor.connection.alias
    
    # Create a mapping of JTService ID to Service ID
    jtservice_to_service_map = {}
    
    for jtservice in JTService.objects.using(db_alias).all():
        # Try to find existing Service by name
        service = Service.objects.using(db_alias).filter(name=jtservice.name).first()
        
        if not service:
            # Create a new Service from JTService data
            service = Service.objects.using(db_alias).create(
                name=jtservice.name,
                description=jtservice.description or '',
                price=jtservice.default_price or Decimal('0.00'),
                hours=jtservice.default_duration_hours or Decimal('0.00'),
                is_active=jtservice.is_active,
                created_by=jtservice.created_by,
                order=0,  # Default order
            )
        
        # Map the old JTService ID to the new Service ID
        jtservice_to_service_map[str(jtservice.id)] = str(service.id)
    
    # Update all JobServiceItem records using raw SQL to bypass foreign key constraints
    # At this point, the foreign key still points to JTService table
    with schema_editor.connection.cursor() as cursor:
        for old_id, new_id in jtservice_to_service_map.items():
            cursor.execute(
                """
                UPDATE jobtracker_app_jobserviceitem 
                SET service_id = %s 
                WHERE service_id = %s
                """,
                [new_id, old_id]
            )
        
        # Set to NULL any items that reference JTService IDs that don't exist in our map
        # (shouldn't happen, but just in case)
        all_jtservice_ids = list(jtservice_to_service_map.keys())
        if all_jtservice_ids:
            placeholders = ','.join(['%s'] * len(all_jtservice_ids))
            cursor.execute(
                f"""
                UPDATE jobtracker_app_jobserviceitem 
                SET service_id = NULL 
                WHERE service_id IS NOT NULL 
                AND service_id NOT IN ({placeholders})
                """,
                all_jtservice_ids
            )


def reverse_migrate(apps, schema_editor):
    """
    Reverse migration - this is complex because we can't easily reverse
    the Service creation. We'll just set service_id to NULL for items that
    were migrated.
    """
    # For reverse, we can't easily restore the JTService references
    # So we'll just leave the service_id as is or set to NULL
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('service_app', '0014_service_hours_service_price'),  # Ensure Service has price and hours fields
        ('jobtracker_app', '0005_alter_job_status'),
    ]

    operations = [
        migrations.RunPython(migrate_jtservice_to_service, reverse_migrate),
    ]

