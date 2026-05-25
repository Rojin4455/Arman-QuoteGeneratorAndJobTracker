from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_location_currency"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ghlauthcredentials",
            name="user_id",
            field=models.CharField(max_length=255),
        ),
    ]
