from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("quote_app", "0029_remove_customersubmission_reschedule_pending_choice"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="customersubmission",
            name="reschedule_of_job",
        ),
    ]
