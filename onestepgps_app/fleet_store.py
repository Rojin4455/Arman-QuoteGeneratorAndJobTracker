"""Normalize OneStep webhook / device payloads into trips and maintenance rows."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from django.utils import timezone

from .alert_store import _as_float, _first, parse_alert_time
from .models import OneStepGPSMaintenance, OneStepGPSTrip

logger = logging.getLogger(__name__)


def _event_type(payload: dict) -> str:
    raw = _first(
        payload.get('event_type'),
        payload.get('type'),
        payload.get('alert_name'),
        payload.get('alert_type'),
        payload.get('Alert Name'),
        '',
    )
    return str(raw or '').strip().lower()


def _event_key(payload: dict, prefix: str) -> str:
    supplied = _first(
        payload.get('event_id'),
        payload.get('alert_id'),
        payload.get('Alert ID'),
        payload.get('drive_id'),
        payload.get('trip_id'),
        payload.get('stop_id'),
        payload.get('id'),
    )
    if supplied:
        return f'{prefix}:{supplied}'[:255]
    digest = hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()[:32]
    return f'{prefix}:{digest}'


def persist_webhook_fleet_events(account, payload: dict) -> dict:
    """Best-effort persist of trip + maintenance from a webhook body."""
    result = {'trip': False, 'maintenance': False}
    if account is None or not isinstance(payload, dict):
        return result
    if persist_trip(account, payload):
        result['trip'] = True
    if persist_maintenance(account, payload):
        result['maintenance'] = True
    return result


def persist_trip(account, payload: dict) -> bool:
    event_type = _event_type(payload)
    started = parse_alert_time(
        _first(
            payload.get('started_at'),
            payload.get('start_time'),
            payload.get('dt_start'),
            payload.get('alert_time_utc'),
            payload.get('alert_time'),
        )
    )
    ended = parse_alert_time(
        _first(payload.get('ended_at'), payload.get('end_time'), payload.get('dt_end'))
    )
    is_stop = 'stop' in event_type
    is_drive = any(k in event_type for k in ('drive', 'trip'))
    has_shape = is_stop or is_drive or (started and ended)
    if not has_shape:
        return False

    device_id = str(_first(payload.get('device_id'), payload.get('Device ID'), '') or '').strip()
    if not device_id or not started:
        return False

    kind = OneStepGPSTrip.KIND_STOP if is_stop else OneStepGPSTrip.KIND_DRIVE
    event_key = _event_key(payload, kind)
    defaults = {
        'kind': kind,
        'device_id': device_id,
        'device_name': str(_first(payload.get('device_name'), payload.get('Device Name'), payload.get('display_name'), '') or '')[:255],
        'started_at': started,
        'ended_at': ended,
        'duration_seconds': _as_float(_first(payload.get('duration_s'), payload.get('duration_seconds'), payload.get('elapsed_s'))),
        'distance_miles': _as_float(_first(payload.get('distance_mi'), payload.get('distance_miles'), payload.get('miles'))),
        'idle_seconds': _as_float(_first(payload.get('idle_s'), payload.get('idle_seconds'))),
        'max_speed_mph': _as_float(_first(payload.get('max_speed_mph'), payload.get('top_speed_mph'))),
        'start_address': str(_first(payload.get('start_address'), '') or '')[:500],
        'end_address': str(_first(payload.get('end_address'), payload.get('stop_address'), '') or '')[:500],
        'start_latitude': _as_float(_first(payload.get('start_lat'), payload.get('start_latitude'))),
        'start_longitude': _as_float(_first(payload.get('start_lng'), payload.get('start_longitude'))),
        'end_latitude': _as_float(_first(payload.get('end_lat'), payload.get('end_latitude'))),
        'end_longitude': _as_float(_first(payload.get('end_lng'), payload.get('end_longitude'))),
        'raw_payload': payload,
    }
    _, created = OneStepGPSTrip.objects.update_or_create(
        account=account,
        event_key=event_key,
        defaults=defaults,
    )
    return created


def _read_dtc_codes(payload: dict) -> list[str]:
    codes = payload.get('dtc_codes') or payload.get('dtcs') or payload.get('fault_codes') or []
    if isinstance(codes, str):
        codes = re.findall(r'[PCBU]\d{4}', codes, flags=re.I) or [c.strip() for c in codes.split(',') if c.strip()]
    elif not isinstance(codes, list):
        codes = []
    out = []
    for code in codes:
        text = str(code).strip().upper()
        if text:
            out.append(text)
    return out


def persist_maintenance(account, payload: dict) -> bool:
    event_type = _event_type(payload)
    codes = _read_dtc_codes(payload)
    has_shape = bool(
        codes
        or re.search(r'dtc|diagnostic|maintenance|fault|engine', event_type)
        or any(k in payload for k in ('engine_hours', 'fuel_level_pct', 'odometer_mi', 'odometer', 'check_engine'))
    )
    device_id = str(_first(payload.get('device_id'), payload.get('Device ID'), '') or '').strip()
    if not has_shape or not device_id:
        return False

    defaults = {
        'device_name': str(_first(payload.get('device_name'), payload.get('Device Name'), payload.get('display_name'), '') or '')[:255],
        'odometer_miles': _as_float(_first(payload.get('odometer_mi'), payload.get('odometer_miles'), payload.get('odometer'), payload.get('Odometer'))),
        'engine_hours': _as_float(_first(payload.get('engine_hours'), payload.get('engine_hour'))),
        'fuel_level_percent': _as_float(_first(payload.get('fuel_level_pct'), payload.get('fuel_percent'), payload.get('fuel_level'))),
        'check_engine': bool(payload.get('check_engine') or payload.get('mil_on') or codes),
        'dtc_codes': codes,
        'raw_payload': payload,
    }
    next_mi = _as_float(_first(payload.get('next_service_mi'), payload.get('service_due_miles')))
    next_at = parse_alert_time(_first(payload.get('next_service_at'), payload.get('service_due_at')))
    existing = OneStepGPSMaintenance.objects.filter(account=account, device_id=device_id).first()
    if not (existing and existing.schedule_managed):
        if next_mi is not None:
            defaults['next_service_miles'] = next_mi
        if next_at is not None:
            defaults['next_service_at'] = next_at
    OneStepGPSMaintenance.objects.update_or_create(
        account=account,
        device_id=device_id,
        defaults=defaults,
    )
    return True


def upsert_maintenance_from_devices(account, devices: list[dict[str, Any]]) -> int:
    if account is None:
        return 0
    updated = 0
    for device in devices or []:
        device_id = str(device.get('device_id') or '').strip()
        if not device_id:
            continue
        odometer = _as_float(device.get('odometer_miles') or device.get('odometer'))
        fuel = _as_float(device.get('fuel_level_percent'))
        hours = _as_float(device.get('engine_hours'))
        codes = device.get('dtc_codes') if isinstance(device.get('dtc_codes'), list) else []
        OneStepGPSMaintenance.objects.update_or_create(
            account=account,
            device_id=device_id,
            defaults={
                'device_name': str(device.get('display_name') or '')[:255],
                'odometer_miles': odometer,
                'engine_hours': hours,
                'fuel_level_percent': fuel,
                'check_engine': bool(device.get('check_engine') or codes),
                'dtc_codes': codes,
            },
        )
        updated += 1
    return updated
