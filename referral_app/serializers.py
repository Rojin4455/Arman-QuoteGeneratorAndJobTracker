from rest_framework import serializers

from referral_app.models import ReferralProgram


class ReferralProgramUpdateSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    reward_mode = serializers.ChoiceField(
        choices=ReferralProgram.REWARD_MODE_CHOICES,
        required=False,
    )
    referrer_reward_cents = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)
    friend_reward_cents = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)
    minimum_invoice_cents = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)
    monthly_referrer_cap_cents = serializers.IntegerField(min_value=0, max_value=1_000_000, required=False)
    invitation_trigger = serializers.ChoiceField(
        choices=ReferralProgram.INVITATION_TRIGGER_CHOICES,
        required=False,
    )
    auto_invite_enabled = serializers.BooleanField(required=False)
    email_invite_enabled = serializers.BooleanField(required=False)
    sms_invite_enabled = serializers.BooleanField(required=False)
    email_delay_minutes = serializers.IntegerField(min_value=0, max_value=43200, required=False)
    sms_delay_minutes = serializers.IntegerField(min_value=0, max_value=43200, required=False)
    email_cadence = serializers.ChoiceField(
        choices=ReferralProgram.INVITE_CADENCE_CHOICES,
        required=False,
    )
    sms_cadence = serializers.ChoiceField(
        choices=ReferralProgram.INVITE_CADENCE_CHOICES,
        required=False,
    )
    primary_color = serializers.CharField(max_length=16, required=False)
    accent_color = serializers.CharField(max_length=16, required=False)
    service_label = serializers.CharField(max_length=80, required=False)
    terms_text = serializers.CharField(required=False, allow_blank=True)


class ReferralClaimSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=80)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)


class EnsureLinkSerializer(serializers.Serializer):
    contact_id = serializers.IntegerField()
