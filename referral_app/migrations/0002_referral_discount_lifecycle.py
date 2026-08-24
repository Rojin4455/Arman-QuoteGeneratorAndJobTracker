from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("referral_app", "0001_initial_referral_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="referralattribution",
            name="referred_phone",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="friend_discount_cents",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="discount_job_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text="Job currently carrying the friend referral discount.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="discount_applied_cents",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Referral discount actually applied on the qualifying invoice.",
            ),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="discount_disabled",
            field=models.BooleanField(
                default=False,
                help_text="Admin manually disabled the referral discount for the qualifying job.",
            ),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="discount_disabled_by",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="reward_credited_cents",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="referralattribution",
            name="reward_credited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
