# Add discount fields to Job: discount_type (amount/percentage), discount_value

from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0018_jobimage_image_optional'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='discount_type',
            field=models.CharField(
                blank=True,
                choices=[('amount', 'Amount (fixed)'), ('percentage', 'Percentage')],
                help_text='Type of discount: fixed amount or percentage of total',
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='job',
            name='discount_value',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='Discount amount in dollars, or percentage (e.g. 10 for 10%%)',
                max_digits=12,
                null=True,
            ),
        ),
    ]
