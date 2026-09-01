from rest_framework import serializers

from .models import OneStepGPSAlert, OneStepGPSIntegration


class OneStepGPSIntegrationSerializer(serializers.ModelSerializer):
    api_key_set = serializers.SerializerMethodField()
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_url = serializers.SerializerMethodField()
    webhook_password_set = serializers.SerializerMethodField()
    webhook_password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = OneStepGPSIntegration
        fields = [
            'id',
            'is_enabled',
            'api_key_set',
            'api_key',
            'webhook_url',
            'webhook_username',
            'webhook_password_set',
            'webhook_password',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'updated_at',
            'api_key_set',
            'webhook_url',
            'webhook_password_set',
        ]

    def get_api_key_set(self, obj):
        return obj.api_key_configured

    def get_webhook_password_set(self, obj):
        return bool((obj.webhook_password or '').strip())

    def get_webhook_url(self, obj):
        request = self.context.get('request')
        location_id = getattr(obj.account, 'location_id', None) or ''
        if not location_id:
            return ''
        path = f'/api/onestepgps/webhook/{location_id}/'
        if request is not None:
            return request.build_absolute_uri(path)
        return path

    def update(self, instance, validated_data):
        api_key = validated_data.pop('api_key', None)
        webhook_password = validated_data.pop('webhook_password', None)
        if api_key is not None:
            instance.api_key = api_key.strip()
        if webhook_password is not None:
            # Allow clearing with empty string
            instance.webhook_password = webhook_password
        return super().update(instance, validated_data)


class OneStepGPSAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneStepGPSAlert
        fields = [
            'id',
            'external_alert_id',
            'alert_name',
            'alert_time',
            'device_id',
            'device_name',
            'drive_status',
            'drive_status_duration_seconds',
            'ignition_on',
            'speed_mph',
            'posted_speed_limit_mph',
            'odometer',
            'external_voltage',
            'latitude',
            'longitude',
            'location_raw',
            'created_at',
        ]
