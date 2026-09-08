from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quote_app', '0034_customersubmission_quote_origin'),
    ]

    operations = [
        migrations.AddField(
            model_name='customersubmission',
            name='technician_notes',
            field=models.TextField(
                blank=True,
                help_text='Private notes for technicians on the signed proposal. Never shown to the customer or on invoices.',
                null=True,
            ),
        ),
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
