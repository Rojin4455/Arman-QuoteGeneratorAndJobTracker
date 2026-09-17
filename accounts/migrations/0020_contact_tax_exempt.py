from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_alter_ghlauthcredentials_user_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="contact",
            name="tax_exempt",
            field=models.BooleanField(
                default=False,
                help_text="If true, invoices for this contact should skip sales tax.",
            ),
        ),
    ]
