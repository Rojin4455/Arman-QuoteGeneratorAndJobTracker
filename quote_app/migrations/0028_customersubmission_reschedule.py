from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobtracker_app", "0022_job_total_surcharge"),
        ("quote_app", "0027_alter_customservice_price_decimal"),
    ]

    operations = [
        migrations.AddField(
            model_name="customersubmission",
            name="reschedule_of_job",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this submission was created as a reschedule quote from that job.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reschedule_submissions",
                to="jobtracker_app.job",
            ),
        ),
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
                    ("rejected", "Rejected"),
                    ("expired", "Expired"),
                    ("reschedule_pending", "Reschedule Pending"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]
