# Generated manually for Job.total_surcharge

from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0021_alter_job_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='total_surcharge',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Surcharge amount (e.g. trip surcharge from location) applied to this job.',
                max_digits=12,
            ),
        ),
    ]
