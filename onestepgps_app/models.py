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
