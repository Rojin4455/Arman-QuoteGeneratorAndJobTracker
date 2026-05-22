from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0023_job_status_reschedule_pending'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='invoice_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='GHL invoice _id created for this job',
                max_length=100,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='invoice_status',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='GHL invoice status (paid, sent, void, etc.)',
                max_length=30,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='job',
            name='invoice_url',
            field=models.URLField(
                blank=True,
                help_text='Public invoice URL for this job',
                max_length=500,
                null=True,
            ),
        ),
    ]
