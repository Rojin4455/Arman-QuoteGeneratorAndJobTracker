from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quote_app', '0026_customersubmission_location'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customservice',
            name='price',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                max_digits=12,
            ),
        ),
    ]
