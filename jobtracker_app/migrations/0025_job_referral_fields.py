from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobtracker_app", "0024_job_invoice_id_status"),
        ("referral_app", "0002_referral_discount_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="referral_attribution",
            field=models.ForeignKey(
                blank=True,
                help_text="Referral attribution when this job belongs to a referred customer.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="jobs",
                to="referral_app.referralattribution",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="apply_referral_discount",
            field=models.BooleanField(
                default=True,
                help_text="Admin can disable the referral discount for this job.",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="referral_discount_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Friend referral discount (dollars) applied to this job.",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="referral_credit_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Referral wallet credit (dollars) applied to this job at invoice time.",
                max_digits=12,
            ),
        ),
    ]
