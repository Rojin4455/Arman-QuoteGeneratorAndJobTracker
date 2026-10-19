from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0027_copy_technician_notes_to_job'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='deposit_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=12,
                help_text='Deposit already received for this job (dollars). Deducted from the invoice at completion.',
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='deposit_note',
            field=models.CharField(
                blank=True, max_length=255, null=True,
                help_text='Deposit date, method or reference (e.g. "Zelle 10/19 ref 123").',
            ),
        ),
    ]
