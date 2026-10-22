from django.db import migrations


class Migration(migrations.Migration):
    """
    Restore the PostgreSQL default on Job.deposit_amount (same issue as 0026).

    Django AddField backfills existing rows then drops the DB DEFAULT, leaving a
    NOT NULL column. Any Job.objects.create() from code that predates the deposit
    field (e.g. gunicorn not yet restarted after migrating) omits the column,
    inserts NULL and fails, so quote scheduling would silently create no Job.
    """

    dependencies = [
        ("jobtracker_app", "0028_job_deposit"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE jobtracker_app_job
                    ALTER COLUMN deposit_amount SET DEFAULT 0.00;
                UPDATE jobtracker_app_job
                    SET deposit_amount = 0.00
                    WHERE deposit_amount IS NULL;
            """,
            reverse_sql="""
                ALTER TABLE jobtracker_app_job
                    ALTER COLUMN deposit_amount DROP DEFAULT;
            """,
        ),
    ]
