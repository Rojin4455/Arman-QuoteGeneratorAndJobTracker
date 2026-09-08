from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0026_job_referral_db_defaults'),
        ('quote_app', '0035_customersubmission_technician_notes'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE jobtracker_app_job AS j
                SET notes = s.technician_notes
                FROM customer_submissions AS s
                WHERE j.submission_id = s.id
                  AND (j.notes IS NULL OR j.notes = '')
                  AND s.technician_notes IS NOT NULL
                  AND TRIM(s.technician_notes) <> '';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
