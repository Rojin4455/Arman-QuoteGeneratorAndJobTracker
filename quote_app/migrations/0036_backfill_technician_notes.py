from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('quote_app', '0035_customersubmission_technician_notes'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE customer_submissions
                SET technician_notes = TRIM(additional_data->>'additional_notes')
                WHERE (technician_notes IS NULL OR technician_notes = '')
                  AND COALESCE(quote_origin, 'technician') <> 'public'
                  AND additional_data ? 'additional_notes'
                  AND NULLIF(TRIM(additional_data->>'additional_notes'), '') IS NOT NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
