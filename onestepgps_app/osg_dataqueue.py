"""Official OneStep GPS DataQueue payload parser.

Docs send an array of:
  { "schema": "alert" | "device_point" | "drive_stop" | "dtc", "value": { ... } }

VERIFY ENDPOINT may GET or POST an empty body / empty array.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from .alert_store import _as_bool, _as_float, parse_alert_time
from .dataqueue import (
    NormalizedAlert,
    NormalizedMaintenance,
    NormalizedTrip,
    OperationalBundle,
    RawEvent,
)

logger = logging.getLogger(__name__)

SCHEMA_ALERT = 'alert'
SCHEMA_DEVICE_POINT = 'device_point'
SCHEMA_DRIVE_STOP = 'drive_stop'
SCHEMA_DTC = 'dtc'


def extract_official_dataqueue_bundle(payload: Any) -> OperationalBundle:
    items = _as_item_list(payload)
    bundle = OperationalBundle()
    seen_alerts: dict[str, NormalizedAlert] = {}
    seen_trips: dict[str, NormalizedTrip] = {}
    seen_maint: dict[str, NormalizedMaintenance] = {}
    seen_vehicles: dict[str, dict[str, Any]] = {}

    for item in items:
        schema, value = _unwrap_schema_item(item)
        if not value:
            continue
        event = _raw_event(schema, value)
        bundle.events.append(event)

        if schema == SCHEMA_ALERT:
            alert = _from_alert(value, event)
            if alert:
                seen_alerts[alert.event_key] = alert
        elif schema == SCHEMA_DRIVE_STOP:
            trip = _from_drive_stop(value, event)
            if trip:
                seen_trips[trip.event_key] = trip
        elif schema == SCHEMA_DTC:
            maint = _from_dtc(value, event)
            if maint:
                seen_maint[maint.device_id] = maint
        elif schema == SCHEMA_DEVICE_POINT:
            vehicle = _from_device_point(value)
            if vehicle and vehicle.get('device_id'):
                seen_vehicles[str(vehicle['device_id'])] = vehicle
            maint = _maintenance_from_device_point(value, event)
            if maint:
                seen_maint[maint.device_id] = maint
            kevent_alert = _alert_from_device_point_events(value, event)
            if kevent_alert:
                seen_alerts[kevent_alert.event_key] = kevent_alert
        else:
            # Unknown schema — still try alert/trip if fields look right
            alert = _from_alert(value, event)
            if alert:
                seen_alerts[alert.event_key] = alert

    bundle.alerts = list(seen_alerts.values())
    bundle.trips = list(seen_trips.values())
    bundle.maintenance = list(seen_maint.values())
    bundle.vehicles = list(seen_vehicles.values())
    return bundle


def _as_item_list(payload: Any) -> list[Any]:
    if payload in (None, '', {}, []):
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if 'schema' in payload and 'value' in payload:
            return [payload]
        for key in ('data', 'items', 'records', 'result_list', 'events'):
            nested = payload.get(key)
            if isinstance(nested, list):
                return nested
        return [payload]
    return []


def _unwrap_schema_item(item: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(item, dict):
        return '', {}
    value = item.get('value')
    schema = str(item.get('schema') or '').strip().lower()
    if isinstance(value, dict):
        return schema or 'onestep_record', value
    # Already flattened (no wrapper)
    return schema or str(item.get('event_type') or item.get('type') or '').lower(), item


def _raw_event(schema: str, value: dict[str, Any]) -> RawEvent:
    device_id = str(_str(value, 'device_id', 'deviceId') or '').strip()
    supplied = _str(
        value,
        'alert_id',
        'dtc_log_id',
        'device_point_id',
        'drive_status_id',
        'event_id',
        'id',
    )
    digest = hashlib.sha256(json.dumps(value, default=str, sort_keys=True).encode()).hexdigest()[:32]
    event_key = f'{schema or "record"}:{supplied or digest}'[:255]
    occurred = parse_alert_time(
        _first(value, 'alert_time_utc', 'dt_tracker', 'dt_server', 'created_at', 'updated_at', 'timestamp')
    )
    return RawEvent(
        event_key=event_key,
        event_type=schema or 'onestep_record',
        device_id=device_id,
        occurred_at=occurred,
        payload=value,
    )


def _from_alert(value: dict[str, Any], event: RawEvent) -> NormalizedAlert | None:
    device_id = event.device_id or _str(value, 'device_id')
    if not device_id:
        return None
    name = _str(value, 'alert_name', 'title', 'alert_type') or 'Alert'
    return NormalizedAlert(
        event_key=event.event_key,
        external_alert_id=str(_str(value, 'alert_id') or event.event_key.split(':', 1)[-1])[:255],
        alert_name=name[:255],
        alert_time=event.occurred_at or parse_alert_time(value.get('alert_time_utc')),
        device_id=device_id,
        device_name=(_str(value, 'device_name', 'display_name') or f'Vehicle {device_id[-6:]}')[:255],
        severity=_severity(name),
        latitude=_as_float(value.get('lat')),
        longitude=_as_float(value.get('lng')),
        speed_mph=_as_float(value.get('speed_mph')),
        acknowledged=False,
        raw_payload=value,
    )


def _from_drive_stop(value: dict[str, Any], event: RawEvent) -> NormalizedTrip | None:
    core = value.get('drive_stop') if isinstance(value.get('drive_stop'), dict) else value
    device_id = event.device_id or _str(value, 'device_id')
    if not device_id:
        return None

    kind_raw = str(core.get('type') or '').lower()
    kind = 'stop' if kind_raw == 'stop' else 'drive'
    started = parse_alert_time(core.get('time_from') or value.get('time_from'))
    ended = parse_alert_time(core.get('time_to') or value.get('time_to'))
    if not started:
        return None

    start_ll = _lat_lng(core.get('lat_lng_from') or core.get('lat_lng_best_first'))
    end_ll = _lat_lng(core.get('lat_lng_to') or core.get('lat_lng_best_last'))
    start_zone = _zone_name(core.get('zone_from_list'))
    end_zone = _zone_name(core.get('zone_to_list'))

    return NormalizedTrip(
        event_key=event.event_key,
        kind=kind,
        device_id=device_id,
        device_name=(_str(value, 'device_name', 'display_name', 'vin') or f'Vehicle {device_id[-6:]}')[:255],
        started_at=started,
        ended_at=ended,
        duration_seconds=_measure_seconds(core.get('duration')),
        distance_miles=_measure_miles(core.get('distance')),
        idle_seconds=_measure_seconds(core.get('idle_duration')),
        max_speed_mph=_measure_mph(core.get('top_speed')),
        start_address=(start_zone or '')[:500],
        end_address=(end_zone or '')[:500],
        start_latitude=start_ll[0],
        start_longitude=start_ll[1],
        end_latitude=end_ll[0],
        end_longitude=end_ll[1],
        raw_payload=value,
    )


def _from_dtc(value: dict[str, Any], event: RawEvent) -> NormalizedMaintenance | None:
    device_id = event.device_id or _str(value, 'device_id')
    if not device_id:
        return None
    code = _str(value, 'code') or _str(value.get('detail_dtc') if isinstance(value.get('detail_dtc'), dict) else {}, 'code')
    codes = [code.upper()] if code else []
    return NormalizedMaintenance(
        device_id=device_id,
        device_name=(_str(value, 'device_name', 'display_name') or f'Vehicle {device_id[-6:]}')[:255],
        odometer_miles=None,
        engine_hours=None,
        fuel_level_percent=None,
        check_engine=bool(codes),
        dtc_codes=codes,
        next_service_miles=None,
        next_service_at=None,
        raw_payload=value,
    )


def _from_device_point(value: dict[str, Any]) -> dict[str, Any] | None:
    device_id = _str(value, 'device_id')
    if not device_id:
        return None
    state = value.get('device_state') if isinstance(value.get('device_state'), dict) else {}
    lat = _as_float(value.get('lat'))
    lng = _as_float(value.get('lng'))
    if lat is None or lng is None:
        loc = _lat_lng(state.get('latest_accurate_point_lat_lng'))
        lat, lng = loc
    speed = _measure_mph(value.get('speed')) or _as_float(value.get('speed_mph'))
    odo = _measure_miles(state.get('odometer') or state.get('software_odometer') or value.get('vbus_odometer'))
    if odo is None:
        odo = _as_float(value.get('odometer'))
    fuel = _as_float(value.get('fuel_percent') or value.get('fuel_level_percent'))
    return {
        'device_id': device_id,
        'display_name': _str(value, 'device_name', 'display_name') or device_id,
        'lat': lat,
        'lng': lng,
        'speed_mph': speed,
        'heading': _as_float(value.get('heading') or value.get('angle')),
        'drive_status': _str(state, 'drive_status') or _str(value, 'drive_status'),
        'odometer_miles': odo,
        'fuel_level_percent': fuel,
        'engine_hours': _as_float(_measure_value(value.get('vbus_total_idle_hours'))),
        'ignition_on': _as_bool(value.get('acc') if 'acc' in value else value.get('ignition_on')),
        'vin': _str(value, 'vin') or _str(state, 'vin'),
        'check_engine': False,
        'dtc_codes': [],
    }


def _maintenance_from_device_point(value: dict[str, Any], event: RawEvent) -> NormalizedMaintenance | None:
    device_id = event.device_id or _str(value, 'device_id')
    if not device_id:
        return None
    vehicle = _from_device_point(value) or {}
    if vehicle.get('odometer_miles') is None and vehicle.get('fuel_level_percent') is None:
        return None
    return NormalizedMaintenance(
        device_id=device_id,
        device_name=str(vehicle.get('display_name') or device_id)[:255],
        odometer_miles=vehicle.get('odometer_miles'),
        engine_hours=vehicle.get('engine_hours'),
        fuel_level_percent=vehicle.get('fuel_level_percent'),
        check_engine=False,
        dtc_codes=[],
        next_service_miles=None,
        next_service_at=None,
        raw_payload=value,
    )


def _alert_from_device_point_events(value: dict[str, Any], event: RawEvent) -> NormalizedAlert | None:
    events = value.get('kevent_list') or value.get('hevent_list') or []
    if not isinstance(events, list) or not events:
        return None
    device_id = event.device_id or _str(value, 'device_id')
    if not device_id:
        return None
    first = events[0] if isinstance(events[0], dict) else {}
    name = str(first.get('hevent_type') or first.get('event_type') or first.get('type') or 'Device event')
    name = name.replace('_', ' ').strip().title()
    return NormalizedAlert(
        event_key=f'device_event:{event.event_key}'[:255],
        external_alert_id=str(_str(value, 'device_point_id') or event.event_key)[:255],
        alert_name=name[:255],
        alert_time=event.occurred_at,
        device_id=device_id,
        device_name=(_str(value, 'device_name', 'display_name') or f'Vehicle {device_id[-6:]}')[:255],
        severity=_severity(name),
        latitude=_as_float(value.get('lat')),
        longitude=_as_float(value.get('lng')),
        speed_mph=_measure_mph(value.get('speed')),
        acknowledged=False,
        raw_payload=value,
    )


def _severity(name: str) -> str:
    text = (name or '').lower()
    if re.search(r'dtc|fault|tamper|disconnect', text):
        return 'critical'
    if re.search(r'engine on|engine off|idle|ignition', text):
        return 'info'
    return 'warning'


def _lat_lng(obj: Any) -> tuple[float | None, float | None]:
    if not isinstance(obj, dict):
        return None, None
    return _as_float(obj.get('lat') or obj.get('latitude')), _as_float(obj.get('lng') or obj.get('longitude'))


def _zone_name(zones: Any) -> str:
    if not isinstance(zones, list) or not zones:
        return ''
    first = zones[0]
    if isinstance(first, dict):
        return str(first.get('name') or first.get('zone_id') or '')
    return str(first)


def _measure_value(obj: Any) -> Any:
    if isinstance(obj, dict) and 'value' in obj:
        return obj.get('value')
    return obj


def _measure_seconds(obj: Any) -> float | None:
    raw = _as_float(_measure_value(obj))
    if raw is None:
        return None
    unit = str(obj.get('unit') or '').lower() if isinstance(obj, dict) else ''
    if unit in ('m', 'min', 'mins', 'minute', 'minutes'):
        return raw * 60
    if unit in ('h', 'hr', 'hrs', 'hour', 'hours'):
        return raw * 3600
    return raw


def _measure_miles(obj: Any) -> float | None:
    raw = _as_float(_measure_value(obj))
    if raw is None:
        return None
    unit = str(obj.get('unit') or '').lower() if isinstance(obj, dict) else ''
    if unit in ('m', 'meter', 'meters'):
        return raw / 1609.34
    if unit in ('km', 'kilometer', 'kilometers'):
        return raw * 0.621371
    return raw


def _measure_mph(obj: Any) -> float | None:
    raw = _as_float(_measure_value(obj))
    if raw is None:
        return None
    unit = str(obj.get('unit') or '').lower() if isinstance(obj, dict) else ''
    if unit in ('km/h', 'kph', 'kmh'):
        return raw * 0.621371
    if unit in ('m/s', 'mps'):
        return raw * 2.23694
    return raw


def _str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first(record: dict[str, Any], *keys: str):
    for key in keys:
        if key in record and record[key] not in (None, ''):
            return record[key]
    return None
