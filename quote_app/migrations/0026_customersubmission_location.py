# Generated manually for location + surcharge on create-submission

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('service_app', '0006_globalsizepackage_servicepackagesizemapping_and_more'),
        ('quote_app', '0025_customersubmission_account'),
    ]

    operations = [
        migrations.AddField(
            model_name='customersubmission',
            name='location',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='customer_submissions',
                to='service_app.location',
                help_text='Service location; when set, trip_surcharge from this location is applied to the quote.',
            ),
        ),
    ]
