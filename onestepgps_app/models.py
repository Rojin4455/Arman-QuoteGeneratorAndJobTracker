import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class OneStepGPSIntegration(models.Model):
    """One Step GPS credentials and settings for a GHL subaccount."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.OneToOneField(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_integration',
    )
    api_key = models.TextField(blank=True, default='')
    is_enabled = models.BooleanField(default=True)
    webhook_username = models.CharField(max_length=255, blank=True, default='')
    webhook_password = models.CharField(max_length=255, blank=True, default='')
    last_webhook_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'onestepgps_integration'

    def __str__(self):
        location = getattr(self.account, 'location_id', None) or self.account_id
        return f'OneStepGPS ({location})'

    @classmethod
    def get_for_account(cls, account):
        if account is None:
            return None
        obj, _ = cls.objects.get_or_create(account=account)
        return obj

    @property
    def api_key_configured(self):
        return bool((self.api_key or '').strip())


class OneStepGPSAlert(models.Model):
    """Persisted One Step GPS webhook alert for Recent / Counts views."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_alerts',
    )
    external_alert_id = models.CharField(max_length=255, blank=True, default='', db_index=True)
    alert_name = models.CharField(max_length=255, blank=True, default='')
    alert_time = models.DateTimeField(null=True, blank=True, db_index=True)
    device_id = models.CharField(max_length=255, blank=True, default='', db_index=True)
    device_name = models.CharField(max_length=255, blank=True, default='')
    drive_status = models.CharField(max_length=255, blank=True, default='')
    drive_status_duration_seconds = models.FloatField(null=True, blank=True)
    ignition_on = models.BooleanField(null=True, blank=True)
    speed_mph = models.FloatField(null=True, blank=True)
    posted_speed_limit_mph = models.FloatField(null=True, blank=True)
    odometer = models.FloatField(null=True, blank=True)
    external_voltage = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    location_raw = models.CharField(max_length=255, blank=True, default='')
    raw_payload = models.JSONField(default=dict, blank=True)
    acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'onestepgps_alert'
        ordering = ['-alert_time', '-created_at']
        indexes = [
            models.Index(fields=['account', '-alert_time']),
            models.Index(fields=['account', 'alert_name', '-alert_time']),
            models.Index(fields=['account', 'external_alert_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'external_alert_id'],
                name='onestepgps_alert_account_external_uniq',
                condition=~Q(external_alert_id=''),
            ),
        ]

    def __str__(self):
        return f'{self.alert_name or "Alert"} — {self.device_name or self.device_id}'

    @property
    def effective_time(self):
        return self.alert_time or self.created_at or timezone.now()

    @property
    def severity(self):
        name = (self.alert_name or '').lower()
        if any(k in name for k in ('dtc', 'fault', 'tamper', 'disconnect', 'engine fault')):
            return 'critical'
        if any(k in name for k in ('idle', 'engine on', 'engine off', 'info')):
            return 'info'
        return 'warning'


class OneStepGPSTrip(models.Model):
    KIND_DRIVE = 'drive'
    KIND_STOP = 'stop'
    KIND_CHOICES = [(KIND_DRIVE, 'Drive'), (KIND_STOP, 'Stop')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_trips',
    )
    event_key = models.CharField(max_length=255, db_index=True)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_DRIVE)
    device_id = models.CharField(max_length=255, db_index=True)
    device_name = models.CharField(max_length=255, blank=True, default='')
    started_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    distance_miles = models.FloatField(null=True, blank=True)
    idle_seconds = models.FloatField(null=True, blank=True)
    max_speed_mph = models.FloatField(null=True, blank=True)
    start_address = models.CharField(max_length=500, blank=True, default='')
    end_address = models.CharField(max_length=500, blank=True, default='')
    start_latitude = models.FloatField(null=True, blank=True)
    start_longitude = models.FloatField(null=True, blank=True)
    end_latitude = models.FloatField(null=True, blank=True)
    end_longitude = models.FloatField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'onestepgps_trip'
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'event_key'],
                name='onestepgps_trip_account_event_uniq',
            ),
        ]


class OneStepGPSMaintenance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_maintenance',
    )
    device_id = models.CharField(max_length=255)
    device_name = models.CharField(max_length=255, blank=True, default='')
    odometer_miles = models.FloatField(null=True, blank=True)
    engine_hours = models.FloatField(null=True, blank=True)
    fuel_level_percent = models.FloatField(null=True, blank=True)
    check_engine = models.BooleanField(default=False)
    dtc_codes = models.JSONField(default=list, blank=True)
    next_service_miles = models.FloatField(null=True, blank=True)
    next_service_at = models.DateTimeField(null=True, blank=True)
    service_type = models.CharField(max_length=64, blank=True, default='')
    interval_miles = models.FloatField(null=True, blank=True)
    interval_days = models.PositiveIntegerField(null=True, blank=True)
    last_service_at = models.DateTimeField(null=True, blank=True)
    last_service_miles = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    schedule_managed = models.BooleanField(default=False)
    raw_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'onestepgps_maintenance'
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'device_id'],
                name='onestepgps_maint_account_device_uniq',
            ),
        ]


class OneStepGPSServiceLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_service_logs',
    )
    maintenance = models.ForeignKey(
        OneStepGPSMaintenance,
        on_delete=models.CASCADE,
        related_name='logs',
    )
    device_id = models.CharField(max_length=255)
    device_name = models.CharField(max_length=255, blank=True, default='')
    service_type = models.CharField(max_length=64, blank=True, default='service')
    performed_at = models.DateTimeField()
    odometer_miles = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'onestepgps_service_log'
        ordering = ['-performed_at', '-created_at']


class OneStepGPSGeofence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_geofences',
    )
    name = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_miles = models.FloatField(default=1.0)
    color = models.CharField(max_length=16, default='#0877f9')
    trigger_entry = models.BooleanField(default=True)
    trigger_exit = models.BooleanField(default=True)
    after_hours = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'onestepgps_geofence'
        ordering = ['name']


class OneStepGPSVehicleBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        'accounts.GHLAuthCredentials',
        on_delete=models.CASCADE,
        related_name='onestepgps_vehicle_bindings',
    )
    device_id = models.CharField(max_length=255)
    user = models.ForeignKey(
        'service_app.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='gps_vehicle_bindings',
    )
    technician_name = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'onestepgps_vehicle_binding'
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'device_id'],
                name='onestepgps_binding_account_device_uniq',
            ),
        ]
