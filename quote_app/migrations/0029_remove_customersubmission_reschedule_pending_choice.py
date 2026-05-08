from django.db import migrations, models


def forwards_reschedule_pending_to_accepted(apps, schema_editor):
    CustomerSubmission = apps.get_model("quote_app", "CustomerSubmission")
    CustomerSubmission.objects.filter(status="reschedule_pending").update(status="accepted")


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("quote_app", "0028_customersubmission_reschedule"),
    ]

    operations = [
        migrations.RunPython(forwards_reschedule_pending_to_accepted, backwards_noop),
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
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]
