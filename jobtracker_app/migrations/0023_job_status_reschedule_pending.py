from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobtracker_app", "0022_job_total_surcharge"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="status",
            field=models.CharField(
                choices=[
                    ("to_convert", "Needs Conversion"),
                    ("reschedule_pending", "Reschedule Pending"),
                    ("pending", "Pending"),
                    ("confirmed", "Confirmed"),
                    ("service_due", "Service Due"),
                    ("on_the_way", "On The Way"),
                    ("in_progress", "In Progress"),
                    ("onhold", "On Hold"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
