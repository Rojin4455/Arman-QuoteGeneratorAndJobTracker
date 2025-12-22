from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quote_app", "0020_add_quoted_by_to_customer_submission"),
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
                    ("rejected", "Rejected"),
                    ("expired", "Expired"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
    ]

