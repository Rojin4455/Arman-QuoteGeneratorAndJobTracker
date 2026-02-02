# Make JobImage.image optional (GHL-only storage; no S3)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0017_jobimage_ghl_file_id_ghl_file_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='jobimage',
            name='image',
            field=models.ImageField(blank=True, help_text='Not used when storing in GHL only', null=True, upload_to='job_images/%Y/%m/%d/'),
        ),
    ]
