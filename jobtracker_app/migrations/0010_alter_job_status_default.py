from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobtracker_app", "0009_add_day_of_week_to_job"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="status",
            field=models.CharField(
                choices=[
                    ("to_convert", "Needs Conversion"),
                    ("pending", "Pending"),
                    ("confirmed", "Confirmed"),
                    ("service_due", "Service Due"),
                    ("on_the_way", "On The Way"),
                    ("in_progress", "In Progress"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]

