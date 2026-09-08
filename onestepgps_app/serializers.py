from rest_framework import serializers

from .models import (
    OneStepGPSAlert,
    OneStepGPSGeofence,
    OneStepGPSIntegration,
    OneStepGPSMaintenance,
    OneStepGPSTrip,
    OneStepGPSVehicleBinding,
)


class OneStepGPSIntegrationSerializer(serializers.ModelSerializer):
    api_key_set = serializers.SerializerMethodField()
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    webhook_url = serializers.SerializerMethodField()
    dataqueue_url = serializers.SerializerMethodField()
    webhook_password_set = serializers.SerializerMethodField()
    webhook_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    dataqueue_auth = serializers.SerializerMethodField()
    last_webhook_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = OneStepGPSIntegration
        fields = [
            'id',
            'is_enabled',
            'api_key_set',
            'api_key',
            'webhook_url',
            'dataqueue_url',
            'webhook_username',
            'webhook_password_set',
            'webhook_password',
            'dataqueue_auth',
            'last_webhook_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'updated_at',
            'api_key_set',
            'webhook_url',
            'dataqueue_url',
            'webhook_password_set',
            'dataqueue_auth',
            'last_webhook_at',
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

    def get_dataqueue_url(self, obj):
        request = self.context.get('request')
        location_id = getattr(obj.account, 'location_id', None) or ''
        if not location_id:
            return ''
        path = f'/api/onestepgps/dataqueue/{location_id}/'
        if request is not None:
            return request.build_absolute_uri(path)
        return path

    def get_dataqueue_auth(self, obj):
        if not (obj.webhook_password or '').strip():
            return ''
        return 'Authorization: Bearer <webhook password>'

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
            'acknowledged',
            'acknowledged_at',
            'severity',
            'created_at',
        ]
        read_only_fields = fields

    severity = serializers.SerializerMethodField()

    def get_severity(self, obj):
        return obj.severity


class OneStepGPSTripSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneStepGPSTrip
        fields = [
            'id',
            'event_key',
            'kind',
            'device_id',
            'device_name',
            'started_at',
            'ended_at',
            'duration_seconds',
            'distance_miles',
            'idle_seconds',
            'max_speed_mph',
            'start_address',
            'end_address',
            'start_latitude',
            'start_longitude',
            'end_latitude',
            'end_longitude',
            'created_at',
        ]


class OneStepGPSMaintenanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneStepGPSMaintenance
        fields = [
            'id',
            'device_id',
            'device_name',
            'odometer_miles',
            'engine_hours',
            'fuel_level_percent',
            'check_engine',
            'dtc_codes',
            'next_service_miles',
            'next_service_at',
            'updated_at',
        ]


class OneStepGPSGeofenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneStepGPSGeofence
        fields = [
            'id',
            'name',
            'latitude',
            'longitude',
            'radius_miles',
            'color',
            'trigger_entry',
            'trigger_exit',
            'after_hours',
            'is_active',
            'created_at',
            'updated_at',
        ]


class OneStepGPSVehicleBindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OneStepGPSVehicleBinding
        fields = ['id', 'device_id', 'user', 'technician_name', 'updated_at']
        extra_kwargs = {'user': {'required': False, 'allow_null': True}}
