# Make CustomerSubmissionImage.image optional (GHL-only storage; no S3)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quote_app', '0023_customersubmissionimage_ghl_file_id_ghl_file_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customersubmissionimage',
            name='image',
            field=models.ImageField(blank=True, help_text='Not used when storing in GHL only', null=True, upload_to='submission_images/%Y/%m/%d/'),
        ),
    ]
