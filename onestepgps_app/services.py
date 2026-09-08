import logging
from typing import Any

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

ONESTEPGPS_API_BASE = 'https://track.onestepgps.com/v3/api/public'
DEVICE_CACHE_SECONDS = 15


class OneStepGPSAPIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _as_float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ('1', 'true', 'yes', 'on'):
        return True
    if s in ('0', 'false', 'no', 'off'):
        return False
    return None


def _extract_lat_lng(point: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(point, dict):
        return None, None
    lat = point.get('lat')
    lng = point.get('lng')
    if lat is None:
        lat = point.get('latitude')
    if lng is None:
        lng = point.get('longitude')
    return _as_float(lat), _as_float(lng)


def _first(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _format_duration_seconds(seconds) -> str | None:
    sec = _as_float(seconds)
    if sec is None or sec < 0:
        return None
    sec = int(sec)
    if sec < 60:
        return f'{sec}s'
    minutes = sec // 60
    if minutes < 60:
        rem = sec % 60
        return f'{minutes}m {rem}s' if rem else f'{minutes}m'
    hours = minutes // 60
    rem_m = minutes % 60
    return f'{hours}h {rem_m}m' if rem_m else f'{hours}h'


def normalize_device(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    point = raw.get('latest_device_point') or raw.get('latest_point') or raw.get('device_point') or {}
    if not isinstance(point, dict):
        point = {}

    lat, lng = _extract_lat_lng(point)
    if lat is None or lng is None:
        lat = _as_float(_first(raw.get('lat'), raw.get('latitude')))
        lng = _as_float(_first(raw.get('lng'), raw.get('longitude')))
    if lat is None or lng is None:
        loc = raw.get('Location (Lat,Lng)') or raw.get('location')
        if isinstance(loc, str) and ',' in loc:
            parts = [p.strip() for p in loc.split(',', 1)]
            if len(parts) == 2:
                lat, lng = _as_float(parts[0]), _as_float(parts[1])
        elif isinstance(loc, dict):
            lat, lng = _extract_lat_lng(loc)

    if lat is None or lng is None:
        return None

    device_id = _first(
        raw.get('device_id'),
        raw.get('factory_id'),
        raw.get('Device ID'),
        raw.get('id'),
    )
    display_name = _first(
        raw.get('display_name'),
        raw.get('device_name'),
        raw.get('Device Name'),
        raw.get('name'),
        str(device_id) if device_id is not None else 'Vehicle',
    )

    speed = _as_float(
        _first(
            point.get('speed'),
            point.get('speed_mph'),
            raw.get('speed'),
            raw.get('Speed (MPH)'),
        )
    )
    heading = _as_float(
        _first(
            point.get('heading'),
            point.get('course'),
            point.get('direction'),
            point.get('bearing'),
            raw.get('heading'),
        )
    )

    drive_status = _first(
        raw.get('drive_status'),
        raw.get('Drive Status'),
        point.get('drive_status'),
        point.get('status'),
    )
    status_duration_seconds = _as_float(
        _first(
            raw.get('drive_status_duration_seconds'),
            raw.get('Drive Status Duration (seconds)'),
            point.get('drive_status_duration_seconds'),
            point.get('status_duration'),
            raw.get('status_duration_seconds'),
        )
    )

    address = _first(
        point.get('address'),
        point.get('formatted_address'),
        raw.get('address'),
        raw.get('Address'),
        raw.get('location_address'),
    )

    tags = raw.get('tags') or raw.get('tag_list') or raw.get('group_names') or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    elif not isinstance(tags, list):
        tags = []
    tags = [str(t) for t in tags if t]

    online = raw.get('online')
    if online is None:
        online = raw.get('active_state') not in ('offline', 'inactive', 'hidden')
    online = bool(online) if online is not None else True

    ignition = _as_bool(
        _first(
            point.get('ignition_on'),
            point.get('ignition'),
            raw.get('ignition_on'),
            raw.get('Ignition On'),
        )
    )

    dtc_raw = raw.get('dtc_codes') or raw.get('dtcs') or point.get('dtc_codes') or []
    if isinstance(dtc_raw, str):
        dtc_codes = [c.strip().upper() for c in dtc_raw.split(',') if c.strip()]
    elif isinstance(dtc_raw, list):
        dtc_codes = [str(c).strip().upper() for c in dtc_raw if c]
    else:
        dtc_codes = []

    return {
        'device_id': str(device_id) if device_id is not None else None,
        'display_name': display_name,
        'lat': lat,
        'lng': lng,
        'online': online,
        'speed_mph': speed,
        'heading': heading,
        'drive_status': drive_status,
        'status_duration_seconds': status_duration_seconds,
        'status_duration_label': _format_duration_seconds(status_duration_seconds),
        'ignition_on': ignition,
        'address': address,
        'tags': tags,
        'last_updated': _first(
            point.get('dt_tracker'),
            point.get('dt'),
            point.get('timestamp'),
            raw.get('updated_at'),
            raw.get('Alert Time'),
        ),
        'factory_id': raw.get('factory_id'),
        'vin': _first(raw.get('vin'), raw.get('VIN')),
        'odometer_miles': _as_float(
            _first(raw.get('odometer_mi'), raw.get('odometer_miles'), raw.get('odometer'), point.get('odometer_mi'))
        ),
        'engine_hours': _as_float(_first(raw.get('engine_hours'), point.get('engine_hours'))),
        'fuel_level_percent': _as_float(
            _first(raw.get('fuel_level_pct'), raw.get('fuel_percent'), raw.get('fuel_level'))
        ),
        'check_engine': bool(raw.get('check_engine') or raw.get('mil_on') or dtc_codes),
        'dtc_codes': dtc_codes,
        'external_voltage': _as_float(
            _first(point.get('external_voltage'), raw.get('External Voltage'), raw.get('external_voltage'))
        ),
    }


def normalize_devices_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get('result_list')
        if items is None:
            items = payload.get('devices') or payload.get('results') or payload.get('data')
        if items is None:
            items = list(payload.values()) if payload else []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    devices = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = normalize_device(item)
        if normalized:
            devices.append(normalized)
    return devices


def _onestep_request(api_key: str, path: str, *, params: dict | None = None) -> Any:
    api_key = (api_key or '').strip()
    if not api_key:
        raise OneStepGPSAPIError('One Step GPS API key is not configured.')

    url = f'{ONESTEPGPS_API_BASE}{path}'
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {api_key}'}
    try:
        response = requests.get(url, params=params or {}, headers=headers, timeout=20)
    except requests.RequestException as exc:
        logger.exception('OneStepGPS request failed path=%s', path)
        raise OneStepGPSAPIError(f'Failed to reach One Step GPS: {exc}') from exc

    if response.status_code == 401:
        raise OneStepGPSAPIError('Invalid One Step GPS API key.', status_code=401)
    if response.status_code >= 400:
        raise OneStepGPSAPIError(
            f'One Step GPS returned HTTP {response.status_code}.',
            status_code=response.status_code,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OneStepGPSAPIError('One Step GPS returned invalid JSON.') from exc


def validate_api_key(api_key: str) -> dict[str, Any]:
    payload = _onestep_request(api_key, '/user/me')
    if not isinstance(payload, dict) or not payload.get('user_id'):
        raise OneStepGPSAPIError('One Step GPS returned an invalid account response.')
    return {
        'user_id': payload.get('user_id'),
        'email': payload.get('email'),
        'customer_id': payload.get('customer_id'),
    }


def fetch_devices_from_api(api_key: str, *, use_cache: bool = True, cache_key: str | None = None) -> list[dict[str, Any]]:
    api_key = (api_key or '').strip()
    if not api_key:
        raise OneStepGPSAPIError('One Step GPS API key is not configured.')

    if use_cache and cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    devices: list[dict[str, Any]] = []
    try:
        params = {
            'active_state': '1',
            'device_id': '1',
            'display_name': '1',
            'drive_status': '1',
            'drive_status_duration_s': '1',
            'dt_tracker': '1',
            'heading': '1',
            'lat_lng': '1',
            'odometer_mi': '1',
            'speed_mph': '1',
            'vin': '1',
        }
        payload = _onestep_request(api_key, '/device-info', params=params)
        devices = normalize_devices_payload(payload)
    except OneStepGPSAPIError as exc:
        logger.warning('OneStepGPS device-info failed, falling back to /device: %s', exc)
        url = f'{ONESTEPGPS_API_BASE}/device'
        params = {'latest_point': 'true', 'api-key': api_key}
        try:
            response = requests.get(url, params=params, timeout=20)
        except requests.RequestException as req_exc:
            raise OneStepGPSAPIError(f'Failed to reach One Step GPS: {req_exc}') from req_exc
        if response.status_code == 401:
            raise OneStepGPSAPIError('Invalid One Step GPS API key.', status_code=401)
        if response.status_code >= 400:
            raise OneStepGPSAPIError(
                f'One Step GPS returned HTTP {response.status_code}.',
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as json_exc:
            raise OneStepGPSAPIError('One Step GPS returned invalid JSON.') from json_exc
        devices = normalize_devices_payload(payload)

    if use_cache and cache_key:
        cache.set(cache_key, devices, timeout=DEVICE_CACHE_SECONDS)
    return devices


def test_api_key(api_key: str) -> dict[str, Any]:
    account = validate_api_key(api_key)
    devices = fetch_devices_from_api(api_key, use_cache=False)
    return {
        'ok': True,
        'device_count': len(devices),
        'account_email': account.get('email'),
    }
