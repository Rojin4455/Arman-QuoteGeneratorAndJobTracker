from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quote_app", "0018_quoteschedule_appointment_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customersubmission",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("responses_completed", "Responses Completed"),
                    ("packages_selected", "Packages Selected"),
                    ("submitted", "Submitted"),
                    ("accepted", "Accepted"),
                    ("expired", "Expired"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]

