# Generated migration to change submission from OneToOneField to ForeignKey

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0012_job_invoice_url'),
        ('quote_app', '0001_initial'),  # Adjust if needed based on your quote_app migrations
    ]

    operations = [
        # Remove the OneToOneField constraint and change to ForeignKey
        migrations.AlterField(
            model_name='job',
            name='submission',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='jobs',  # Changed from 'job' to 'jobs' (plural)
                to='quote_app.customersubmission'
            ),
        ),
    ]

