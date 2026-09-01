from django.contrib import admin

from .models import OneStepGPSAlert, OneStepGPSIntegration


@admin.register(OneStepGPSIntegration)
class OneStepGPSIntegrationAdmin(admin.ModelAdmin):
    list_display = ('account', 'is_enabled', 'api_key_configured', 'updated_at')
    list_filter = ('is_enabled',)
    search_fields = ('account__location_id', 'account__company_name')
    readonly_fields = ('updated_at',)


@admin.register(OneStepGPSAlert)
class OneStepGPSAlertAdmin(admin.ModelAdmin):
    list_display = (
        'alert_name',
        'device_name',
        'alert_time',
        'account',
        'external_alert_id',
        'created_at',
    )
    list_filter = ('alert_name',)
    search_fields = ('alert_name', 'device_name', 'device_id', 'external_alert_id')
    readonly_fields = ('created_at', 'raw_payload')
    date_hierarchy = 'alert_time'
