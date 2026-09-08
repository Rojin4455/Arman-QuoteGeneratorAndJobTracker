from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('onestepgps_app', '0003_fleet_center'),
    ]

    operations = [
        migrations.AddField(
            model_name='onestepgpsintegration',
            name='last_webhook_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
