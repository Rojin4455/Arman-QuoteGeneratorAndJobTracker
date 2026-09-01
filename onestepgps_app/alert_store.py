"""Helpers for parsing and storing One Step GPS webhook alerts.

Supports both payload shapes:
1) Legacy spaced keys: "Alert ID", "Alert Name", "Location (Lat,Lng)", ...
2) Current snake_case test/live API: alert_id, alert_name, alert_time_utc, lat, lng, ...
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import OneStepGPSAlert

logger = logging.getLogger(__name__)


def _as_float(value):
    if value is None or value == '':
        return None
    if isinstance(value, dict):
        # e.g. {"value": 20, "unit": "mph"}
        return _as_float(value.get('value'))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ('1', 'true', 'yes', 'on'):
        return True
    if s in ('0', 'false', 'no', 'off'):
        return False
    return None


def _first(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def parse_alert_time(value) -> datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value, timezone.utc)

    text = str(value).strip()
    if text.endswith('Z'):
        text_iso = text[:-1] + '+00:00'
    else:
        text_iso = text

    dt = parse_datetime(text_iso)
    if dt is not None:
        return dt if timezone.is_aware(dt) else timezone.make_aware(dt, timezone.utc)

    for fmt in (
        '%m/%d/%Y %I:%M:%S %p',
        '%m/%d/%Y %H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%m/%d/%y %I:%M:%S %p',
    ):
        try:
            naive = datetime.strptime(text, fmt)
            return timezone.make_aware(naive, timezone.get_current_timezone())
        except ValueError:
            continue
    return None


def parse_lat_lng(value) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if isinstance(value, dict):
        # Nested device_point or lat_lng object
        if 'lat' in value or 'lng' in value or 'latitude' in value:
            return _as_float(value.get('lat') or value.get('latitude')), _as_float(
                value.get('lng') or value.get('longitude')
            )
        nested = value.get('lat_lng') or value.get('adjusted_lat_lng') or {}
        if isinstance(nested, dict):
            return _as_float(nested.get('lat') or nested.get('latitude')), _as_float(
                nested.get('lng') or nested.get('longitude')
            )
        return None, None
    text = str(value).strip()
    if ',' not in text:
        return None, None
    left, right = text.split(',', 1)
    return _as_float(left.strip()), _as_float(right.strip())


def _duration_seconds(payload: dict) -> float | None:
    direct = _as_float(
        _first(
            payload.get('drive_status_duration_s'),
            payload.get('drive_status_duration_seconds'),
            payload.get('Drive Status Duration (seconds)'),
        )
    )
    if direct is not None:
        return direct

    # Nested: device_point.device_state.drive_status_duration = {value, unit}
    point = payload.get('device_point')
    if isinstance(point, dict):
        state = point.get('device_state') or {}
        if isinstance(state, dict):
            dur = state.get('drive_status_duration')
            if isinstance(dur, dict):
                value = _as_float(dur.get('value'))
                unit = str(dur.get('unit') or '').lower()
                if value is None:
                    return None
                if unit in ('s', 'sec', 'secs', 'second', 'seconds'):
                    return value
                if unit in ('m', 'min', 'mins', 'minute', 'minutes'):
                    return value * 60
                if unit in ('h', 'hr', 'hrs', 'hour', 'hours'):
                    return value * 3600
                return value
    return None


def canonicalize_webhook_payload(payload: dict) -> dict:
    """Normalize legacy + modern One Step GPS webhook bodies into one shape."""
    if not isinstance(payload, dict):
        return {}

    point = payload.get('device_point') if isinstance(payload.get('device_point'), dict) else {}
    detail = point.get('device_point_detail') if isinstance(point.get('device_point_detail'), dict) else {}
    state = point.get('device_state') if isinstance(point.get('device_state'), dict) else {}

    lat = _as_float(_first(payload.get('lat'), payload.get('latitude')))
    lng = _as_float(_first(payload.get('lng'), payload.get('longitude')))
    location_raw = payload.get('Location (Lat,Lng)') or ''
    if lat is None or lng is None:
        lat, lng = parse_lat_lng(location_raw)
    if lat is None or lng is None:
        lat, lng = parse_lat_lng(payload.get('Device Point') or point)
    if lat is None or lng is None:
        lat, lng = parse_lat_lng(detail.get('lat_lng'))

    speed = _as_float(
        _first(
            payload.get('speed_mph'),
            payload.get('Speed (MPH)'),
            payload.get('speed'),
            point.get('speed'),
            detail.get('speed'),
        )
    )

    drive_status = _first(
        payload.get('drive_status'),
        payload.get('Drive Status'),
        state.get('drive_status'),
    )

    ignition = _as_bool(
        _first(
            payload.get('ignition_on'),
            payload.get('Ignition On'),
            detail.get('acc'),
            detail.get('vbus_engine_on'),
        )
    )

    voltage = _as_float(
        _first(
            payload.get('external_voltage'),
            payload.get('External Voltage'),
            detail.get('external_volt'),
        )
    )

    alert_id = str(
        _first(payload.get('alert_id'), payload.get('Alert ID'), '') or ''
    ).strip()
    alert_name = str(
        _first(payload.get('alert_name'), payload.get('Alert Name'), '') or ''
    ).strip()[:255]
    device_id = str(
        _first(payload.get('device_id'), payload.get('Device ID'), '') or ''
    ).strip()[:255]
    device_name = str(
        _first(payload.get('device_name'), payload.get('Device Name'), '') or ''
    ).strip()[:255]

    alert_time = parse_alert_time(
        _first(
            payload.get('alert_time_utc'),
            payload.get('alert_time'),
            payload.get('Alert Time'),
        )
    )

    if location_raw:
        location_display = str(location_raw)
    elif lat is not None and lng is not None:
        location_display = f'{lat},{lng}'
    else:
        location_display = ''

    return {
        'external_alert_id': alert_id,
        'alert_name': alert_name,
        'alert_time': alert_time,
        'device_id': device_id,
        'device_name': device_name,
        'drive_status': str(drive_status or '')[:255],
        'drive_status_duration_seconds': _duration_seconds(payload),
        'ignition_on': ignition,
        'speed_mph': speed,
        'posted_speed_limit_mph': _as_float(
            _first(
                payload.get('posted_speed_limit_mph'),
                payload.get('Posted Speed Limit (MPH)'),
            )
        ),
        'odometer': _as_float(_first(payload.get('odometer'), payload.get('Odometer'))),
        'external_voltage': voltage,
        'latitude': lat,
        'longitude': lng,
        'location_raw': location_display[:255],
    }


def normalize_webhook_payload(data: Any) -> dict:
    """Compact summary for logs (works for both formats)."""
    if not isinstance(data, dict):
        return {}
    c = canonicalize_webhook_payload(data)
    return {
        'alert_id': c.get('external_alert_id'),
        'alert_name': c.get('alert_name'),
        'device_id': c.get('device_id'),
        'device_name': c.get('device_name'),
        'alert_time': c.get('alert_time').isoformat() if c.get('alert_time') else None,
        'lat': c.get('latitude'),
        'lng': c.get('longitude'),
        'speed_mph': c.get('speed_mph'),
        'drive_status': c.get('drive_status'),
        'ignition_on': c.get('ignition_on'),
    }


def persist_webhook_alert(account, payload: dict) -> tuple[OneStepGPSAlert | None, bool]:
    """
    Save webhook payload for account.
    Returns (alert, created). created=False when duplicate alert_id.
    """
    if account is None or not isinstance(payload, dict):
        return None, False

    fields = canonicalize_webhook_payload(payload)
    external_alert_id = fields['external_alert_id']

    if external_alert_id:
        existing = OneStepGPSAlert.objects.filter(
            account=account,
            external_alert_id=external_alert_id,
        ).first()
        if existing:
            return existing, False

    try:
        with transaction.atomic():
            alert = OneStepGPSAlert.objects.create(
                account=account,
                external_alert_id=external_alert_id,
                alert_name=fields['alert_name'],
                alert_time=fields['alert_time'] or timezone.now(),
                device_id=fields['device_id'],
                device_name=fields['device_name'],
                drive_status=fields['drive_status'],
                drive_status_duration_seconds=fields['drive_status_duration_seconds'],
                ignition_on=fields['ignition_on'],
                speed_mph=fields['speed_mph'],
                posted_speed_limit_mph=fields['posted_speed_limit_mph'],
                odometer=fields['odometer'],
                external_voltage=fields['external_voltage'],
                latitude=fields['latitude'],
                longitude=fields['longitude'],
                location_raw=fields['location_raw'],
                raw_payload=payload,
            )
        return alert, True
    except IntegrityError:
        existing = OneStepGPSAlert.objects.filter(
            account=account,
            external_alert_id=external_alert_id,
        ).first()
        return existing, False
