"""Parse OneStep GPS DataQueue / batch webhook payloads (ported from prototype dataqueue.ts)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .alert_store import _as_bool, _as_float, _first, parse_alert_time
from .services import normalize_device

CONTAINER_KEYS = (
    'result_list', 'data', 'items', 'records', 'events', 'alerts', 'drives', 'stops',
    'diagnostics', 'dtcs', 'device_points', 'points',
)

UNWRAP_KEYS = ('event', 'alert', 'drive', 'trip', 'stop', 'diagnostic', 'dtc')

ALERT_TYPE_RE = re.compile(
    r'(alert|speed|geofence|harsh|brak|accel|corner|tamper|after.?hours|idle|dtc|fault|engine)',
    re.I,
)
MAINT_TYPE_RE = re.compile(r'(dtc|diagnostic|maintenance|fault|engine)', re.I)
DTC_CODE_RE = re.compile(r'[PCBU]\d{4}', re.I)


@dataclass
class RawEvent:
    event_key: str
    event_type: str
    device_id: str
    occurred_at: Any
    payload: dict


@dataclass
class NormalizedAlert:
    event_key: str
    external_alert_id: str
    alert_name: str
    alert_time: Any
    device_id: str
    device_name: str
    severity: str
    latitude: float | None
    longitude: float | None
    speed_mph: float | None
    acknowledged: bool
    raw_payload: dict


@dataclass
class NormalizedTrip:
    event_key: str
    kind: str
    device_id: str
    device_name: str
    started_at: Any
    ended_at: Any
    duration_seconds: float | None
    distance_miles: float | None
    idle_seconds: float | None
    max_speed_mph: float | None
    start_address: str
    end_address: str
    start_latitude: float | None
    start_longitude: float | None
    end_latitude: float | None
    end_longitude: float | None
    raw_payload: dict


@dataclass
class NormalizedMaintenance:
    device_id: str
    device_name: str
    odometer_miles: float | None
    engine_hours: float | None
    fuel_level_percent: float | None
    check_engine: bool
    dtc_codes: list[str]
    next_service_miles: float | None
    next_service_at: Any
    raw_payload: dict


@dataclass
class OperationalBundle:
    events: list[RawEvent] = field(default_factory=list)
    alerts: list[NormalizedAlert] = field(default_factory=list)
    trips: list[NormalizedTrip] = field(default_factory=list)
    maintenance: list[NormalizedMaintenance] = field(default_factory=list)
    vehicles: list[dict[str, Any]] = field(default_factory=list)


def extract_dataqueue_bundle(payload: Any) -> OperationalBundle:
    records = collect_records(payload)
    bundle = OperationalBundle()
    seen_alerts: dict[str, NormalizedAlert] = {}
    seen_trips: dict[str, NormalizedTrip] = {}
    seen_maint: dict[str, NormalizedMaintenance] = {}
    seen_vehicles: dict[str, dict[str, Any]] = {}

    for original in records:
        record = unwrap_record(original)
        event = normalize_raw_event(record)
        bundle.events.append(event)

        raw_vehicle = normalize_dataqueue_record(record)
        vehicle = normalize_device(raw_vehicle)
        if vehicle and vehicle.get('device_id'):
            seen_vehicles[str(vehicle['device_id'])] = vehicle

        trip = normalize_trip(record, event)
        if trip:
            seen_trips[trip.event_key] = trip

        alert = normalize_alert(record, event)
        if alert:
            seen_alerts[alert.event_key] = alert

        maint = normalize_maintenance(record, event)
        if maint:
            seen_maint[maint.device_id] = maint

    bundle.alerts = list(seen_alerts.values())
    bundle.trips = list(seen_trips.values())
    bundle.maintenance = list(seen_maint.values())
    bundle.vehicles = list(seen_vehicles.values())
    return bundle


def is_batch_payload(payload: Any) -> bool:
    if isinstance(payload, list):
        return len(payload) > 0
    if not isinstance(payload, dict):
        return False
    if any(key in payload for key in CONTAINER_KEYS):
        return True
    return len(collect_records(payload)) > 1


def collect_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for item in payload:
            out.extend(collect_records(item))
        return out
    if not isinstance(payload, dict):
        return []
    nested: list[dict[str, Any]] = []
    for key in CONTAINER_KEYS:
        if key in payload:
            nested.extend(collect_records(payload[key]))
    return nested if nested else [payload]


def unwrap_record(record: dict[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    for key in UNWRAP_KEYS:
        nested = record.get(key)
        if isinstance(nested, dict):
            merged = {**record, **nested}
            break
    return merged


def normalize_raw_event(record: dict[str, Any]) -> RawEvent:
    event_type = detect_event_type(record)
    device_id = str(_first_string(record, 'device_id', 'deviceId', 'tracker_id') or '').strip()
    occurred_at = _first_date(record, 'occurred_at', 'event_time', 'dt_tracker', 'timestamp', 'started_at', 'start_time', 'created_at', 'alert_time_utc', 'alert_time')
    supplied = _first_string(record, 'event_id', 'alert_id', 'drive_id', 'trip_id', 'stop_id', 'point_id', 'id', 'Alert ID')
    digest = hashlib.sha256(json.dumps(record, default=str, sort_keys=True).encode()).hexdigest()[:32]
    event_key = f'{event_type}:{supplied}' if supplied else f'{event_type}:{digest}'
    return RawEvent(
        event_key=event_key[:255],
        event_type=event_type,
        device_id=device_id,
        occurred_at=occurred_at,
        payload=record,
    )


def normalize_trip(record: dict[str, Any], event: RawEvent) -> NormalizedTrip | None:
    event_type = event.event_type
    is_stop = 'stop' in event_type
    started_at = _first_date(record, 'started_at', 'start_time', 'dt_start', 'occurred_at', 'timestamp', 'alert_time_utc')
    ended_at = _first_date(record, 'ended_at', 'end_time', 'dt_end')
    has_trip_shape = bool(
        is_stop
        or 'drive' in event_type
        or 'trip' in event_type
        or (started_at and ended_at)
    )
    if not has_trip_shape or not event.device_id or not started_at:
        return None

    return NormalizedTrip(
        event_key=event.event_key,
        kind='stop' if is_stop else 'drive',
        device_id=event.device_id,
        device_name=_device_name(record, event.device_id),
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=_as_float(_first(record, 'duration_s', 'duration_seconds', 'elapsed_s')),
        distance_miles=_as_float(_first(record, 'distance_mi', 'distance_miles', 'miles')),
        idle_seconds=_as_float(_first(record, 'idle_s', 'idle_seconds', 'idle_duration_s')),
        max_speed_mph=_as_float(_first(record, 'max_speed_mph', 'top_speed_mph')),
        start_address=str(_first(record, 'start_address') or '')[:500],
        end_address=str(_first(record, 'end_address', 'stop_address') or '')[:500],
        start_latitude=_as_float(_first(record, 'start_lat', 'start_latitude', 'lat', 'latitude')),
        start_longitude=_as_float(_first(record, 'start_lng', 'start_longitude', 'lng', 'longitude')),
        end_latitude=_as_float(_first(record, 'end_lat', 'end_latitude')),
        end_longitude=_as_float(_first(record, 'end_lng', 'end_longitude')),
        raw_payload=record,
    )


def normalize_alert(record: dict[str, Any], event: RawEvent) -> NormalizedAlert | None:
    alert_type = _first_string(record, 'alert_type', 'alert_name', 'rule_type', 'Alert Name', 'title') or event.event_type
    has_alert_name = bool(_first_string(record, 'alert_type', 'alert_name', 'rule_type', 'Alert Name', 'Alert ID', 'alert_id', 'title'))
    is_alert = bool(
        has_alert_name
        or ALERT_TYPE_RE.search(event.event_type or '')
        or ALERT_TYPE_RE.search(str(alert_type or ''))
    )
    if not is_alert or not event.device_id:
        return None

    severity_text = (_first_string(record, 'severity', 'priority') or '').lower()
    if severity_text in ('critical', 'high') or re.search(r'dtc|fault|tamper|disconnect', str(alert_type), re.I):
        severity = 'critical'
    elif severity_text in ('info', 'low') or re.search(r'engine on|engine off|idle', str(alert_type), re.I):
        severity = 'info'
    else:
        severity = 'warning'

    external_id = _first_string(record, 'alert_id', 'Alert ID', 'event_id', 'id') or event.event_key.split(':', 1)[-1]
    alert_time = event.occurred_at or parse_alert_time(_first(record, 'alert_time_utc', 'alert_time', 'Alert Time'))

    return NormalizedAlert(
        event_key=event.event_key,
        external_alert_id=str(external_id or '')[:255],
        alert_name=str(alert_type or 'Alert')[:255],
        alert_time=alert_time,
        device_id=event.device_id,
        device_name=_device_name(record, event.device_id),
        severity=severity,
        latitude=_as_float(_first(record, 'lat', 'latitude')),
        longitude=_as_float(_first(record, 'lng', 'longitude')),
        speed_mph=_as_float(_first(record, 'speed_mph', 'Speed (MPH)', 'speed')),
        acknowledged=bool(_as_bool(_first(record, 'acknowledged', 'resolved'))),
        raw_payload=record,
    )


def normalize_maintenance(record: dict[str, Any], event: RawEvent) -> NormalizedMaintenance | None:
    codes = _read_dtc_codes(record)
    has_shape = bool(
        codes
        or MAINT_TYPE_RE.search(event.event_type or '')
        or any(key in record for key in ('engine_hours', 'fuel_level_pct', 'odometer_mi', 'odometer', 'check_engine', 'Odometer'))
    )
    if not has_shape or not event.device_id:
        return None

    return NormalizedMaintenance(
        device_id=event.device_id,
        device_name=_device_name(record, event.device_id),
        odometer_miles=_as_float(_first(record, 'odometer_mi', 'odometer_miles', 'odometer', 'Odometer')),
        engine_hours=_as_float(_first(record, 'engine_hours', 'engine_hour', 'hours')),
        fuel_level_percent=_as_float(_first(record, 'fuel_level_pct', 'fuel_percent', 'fuel_level')),
        check_engine=bool(_as_bool(_first(record, 'check_engine', 'mil_on')) or codes),
        dtc_codes=codes,
        next_service_miles=_as_float(_first(record, 'next_service_mi', 'service_due_miles')),
        next_service_at=_first_date(record, 'next_service_at', 'service_due_at'),
        raw_payload=record,
    )


def normalize_dataqueue_record(record: dict[str, Any]) -> dict[str, Any]:
    point = record.get('device_point') if isinstance(record.get('device_point'), dict) else record
    state = record.get('device_state') if isinstance(record.get('device_state'), dict) else {}
    if isinstance(point, dict) and isinstance(point.get('device_state'), dict):
        state = point.get('device_state')
    detail = record.get('device_point_detail') if isinstance(record.get('device_point_detail'), dict) else {}
    if isinstance(point, dict) and isinstance(point.get('device_point_detail'), dict):
        detail = point.get('device_point_detail')
    adjusted = state.get('adjusted_lat_lng') if isinstance(state.get('adjusted_lat_lng'), dict) else {}
    detail_lat_lng = detail.get('lat_lng') if isinstance(detail.get('lat_lng'), dict) else {}
    detail_speed = detail.get('speed') if isinstance(detail.get('speed'), dict) else {}

    merged = {**record}
    if isinstance(point, dict):
        merged.update(point)

    merged['device_id'] = _first_string(record, 'device_id', 'deviceId', 'Device ID') or _first_string(point, 'device_id', 'deviceId')
    merged['display_name'] = _first_string(record, 'display_name', 'device_name', 'Device Name') or _first_string(point, 'display_name', 'device_name')
    merged['drive_status'] = _first_string(record, 'drive_status', 'Drive Status') or _first_string(state, 'drive_status')
    merged['drive_status_duration_s'] = _first(record, 'drive_status_duration_s', 'drive_status_duration_seconds') or _first(state, 'drive_status_duration_s')
    merged['dt_tracker'] = _first(record, 'dt_tracker', 'occurred_at', 'timestamp') or _first(point, 'dt_tracker', 'timestamp')
    merged['heading'] = _first(record, 'heading') or _first(point, 'heading') or _first(detail, 'heading')
    merged['lat'] = (
        _first(record, 'lat', 'latitude')
        or _first(point, 'lat', 'latitude')
        or _first(adjusted, 'lat')
        or _first(detail_lat_lng, 'lat')
    )
    merged['lng'] = (
        _first(record, 'lng', 'longitude')
        or _first(point, 'lng', 'longitude')
        or _first(adjusted, 'lng')
        or _first(detail_lat_lng, 'lng')
    )
    merged['odometer_mi'] = _first(record, 'odometer_mi', 'odometer_miles', 'odometer') or _first(point, 'odometer_mi', 'odometer_miles')
    merged['speed_mph'] = _first(record, 'speed_mph') or _first(point, 'speed_mph') or _first(detail_speed, 'value')
    merged['vin'] = _first_string(record, 'vin', 'VIN') or _first_string(point, 'vin', 'VIN')
    return merged


def detect_event_type(record: dict[str, Any]) -> str:
    explicit = _first_string(
        record, 'event_type', 'record_type', 'type', 'activity_type', 'queue_type', 'alert_type',
    )
    if explicit:
        return re.sub(r'[^a-z0-9]+', '_', explicit.lower()).strip('_')
    if 'dtc_codes' in record or 'dtc_code' in record:
        return 'diagnostic_trouble_code'
    if 'device_point' in record or 'dt_tracker' in record:
        return 'device_point'
    return 'onestep_record'


def _read_dtc_codes(record: dict[str, Any]) -> list[str]:
    value = record.get('dtc_codes') or record.get('dtc_code') or record.get('fault_codes')
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip().upper())
            elif isinstance(item, dict):
                code = _first_string(item, 'code', 'dtc')
                if code:
                    out.append(code.upper())
        return out
    if isinstance(value, str):
        found = DTC_CODE_RE.findall(value)
        if found:
            return [c.upper() for c in found]
        return [part.strip().upper() for part in re.split(r'[;,\s]+', value) if part.strip()]
    return []


def _first_string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_date(record: dict[str, Any], *keys: str):
    for key in keys:
        value = record.get(key)
        if value is None or value == '':
            continue
        parsed = parse_alert_time(value)
        if parsed is not None:
            return parsed
    return None


def _device_name(record: dict[str, Any], device_id: str) -> str:
    name = _first_string(record, 'display_name', 'vehicle_name', 'device_name', 'Device Name')
    if name:
        return name[:255]
    suffix = device_id[-6:] if device_id else 'unknown'
    return f'Vehicle {suffix}'
