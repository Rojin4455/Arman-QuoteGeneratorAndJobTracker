# Generated manually for GHL media fields on JobImage

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0016_job_contact_address'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobimage',
            name='ghl_file_id',
            field=models.CharField(blank=True, help_text='GHL media document ID after upload', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='jobimage',
            name='ghl_file_url',
            field=models.URLField(blank=True, help_text='GHL media file URL after upload', max_length=500, null=True),
        ),
    ]
