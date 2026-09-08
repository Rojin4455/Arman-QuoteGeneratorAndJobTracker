"""Persist parsed DataQueue operational bundles into Django models."""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .dataqueue import NormalizedAlert, NormalizedMaintenance, NormalizedTrip, OperationalBundle
from .fleet_store import upsert_maintenance_from_devices
from .models import OneStepGPSAlert, OneStepGPSMaintenance, OneStepGPSTrip

logger = logging.getLogger(__name__)


def persist_operational_bundle(account, bundle: OperationalBundle) -> dict[str, int]:
    if account is None:
        return {'events': 0, 'alerts': 0, 'trips': 0, 'maintenance': 0, 'vehicles': 0}

    stats = {'events': len(bundle.events), 'alerts': 0, 'trips': 0, 'maintenance': 0, 'vehicles': 0}

    for alert in bundle.alerts:
        if _persist_bundle_alert(account, alert):
            stats['alerts'] += 1

    for trip in bundle.trips:
        if _persist_bundle_trip(account, trip):
            stats['trips'] += 1

    for maint in bundle.maintenance:
        if _persist_bundle_maintenance(account, maint):
            stats['maintenance'] += 1

    if bundle.vehicles:
        stats['vehicles'] = upsert_maintenance_from_devices(account, bundle.vehicles)

    return stats


def _persist_bundle_alert(account, alert: NormalizedAlert) -> bool:
    external_id = (alert.external_alert_id or '').strip()
    if external_id:
        existing = OneStepGPSAlert.objects.filter(account=account, external_alert_id=external_id).first()
        if existing:
            return False

    try:
        with transaction.atomic():
            OneStepGPSAlert.objects.create(
                account=account,
                external_alert_id=external_id,
                alert_name=alert.alert_name,
                alert_time=alert.alert_time or timezone.now(),
                device_id=alert.device_id,
                device_name=alert.device_name,
                speed_mph=alert.speed_mph,
                latitude=alert.latitude,
                longitude=alert.longitude,
                acknowledged=alert.acknowledged,
                raw_payload=alert.raw_payload,
            )
        return True
    except IntegrityError:
        return False


def _persist_bundle_trip(account, trip: NormalizedTrip) -> bool:
    defaults = {
        'kind': trip.kind,
        'device_id': trip.device_id,
        'device_name': trip.device_name,
        'started_at': trip.started_at,
        'ended_at': trip.ended_at,
        'duration_seconds': trip.duration_seconds,
        'distance_miles': trip.distance_miles,
        'idle_seconds': trip.idle_seconds,
        'max_speed_mph': trip.max_speed_mph,
        'start_address': trip.start_address,
        'end_address': trip.end_address,
        'start_latitude': trip.start_latitude,
        'start_longitude': trip.start_longitude,
        'end_latitude': trip.end_latitude,
        'end_longitude': trip.end_longitude,
        'raw_payload': trip.raw_payload,
    }
    _, created = OneStepGPSTrip.objects.update_or_create(
        account=account,
        event_key=trip.event_key,
        defaults=defaults,
    )
    return created


def _persist_bundle_maintenance(account, maint: NormalizedMaintenance) -> bool:
    existing = OneStepGPSMaintenance.objects.filter(account=account, device_id=maint.device_id).first()
    defaults = {
        'device_name': maint.device_name,
        'odometer_miles': maint.odometer_miles,
        'engine_hours': maint.engine_hours,
        'fuel_level_percent': maint.fuel_level_percent,
        'check_engine': maint.check_engine,
        'dtc_codes': maint.dtc_codes,
        'raw_payload': maint.raw_payload,
    }
    if not (existing and existing.schedule_managed):
        if maint.next_service_miles is not None:
            defaults['next_service_miles'] = maint.next_service_miles
        if maint.next_service_at is not None:
            defaults['next_service_at'] = maint.next_service_at
    obj, created = OneStepGPSMaintenance.objects.update_or_create(
        account=account,
        device_id=maint.device_id,
        defaults=defaults,
    )
    return created or bool(maint.odometer_miles or maint.fuel_level_percent or maint.engine_hours or maint.dtc_codes)
