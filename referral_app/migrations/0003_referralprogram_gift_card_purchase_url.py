from django.db import migrations, models

TRUSHINE_LOCATION_ID = "b8qvo7VooP3JD3dIZU42"
TRUSHINE_GIFT_CARD_URL = "https://links.theservicepilot.com/gift-card/6a8db5dd206904b75135e9b9"


def seed_trushine_gift_card_url(apps, schema_editor):
    GHLAuthCredentials = apps.get_model("accounts", "GHLAuthCredentials")
    ReferralProgram = apps.get_model("referral_app", "ReferralProgram")

    account = GHLAuthCredentials.objects.filter(location_id=TRUSHINE_LOCATION_ID).first()
    if not account:
        return

    program, _ = ReferralProgram.objects.get_or_create(account=account)
    if not (program.gift_card_purchase_url or "").strip():
        program.gift_card_purchase_url = TRUSHINE_GIFT_CARD_URL
        program.save(update_fields=["gift_card_purchase_url"])


class Migration(migrations.Migration):

    dependencies = [
        ("referral_app", "0002_referral_discount_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="referralprogram",
            name="gift_card_purchase_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Customer-facing gift card purchase page URL for this subaccount.",
                max_length=500,
            ),
        ),
        migrations.RunPython(seed_trushine_gift_card_url, migrations.RunPython.noop),
    ]
