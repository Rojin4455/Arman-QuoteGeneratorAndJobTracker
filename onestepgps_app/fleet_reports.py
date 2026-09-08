from collections import defaultdict
from datetime import timedelta

from django.db import connection
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .maintenance_utils import due_info
from .models import (
    OneStepGPSAlert,
    OneStepGPSGeofence,
    OneStepGPSMaintenance,
    OneStepGPSServiceLog,
    OneStepGPSTrip,
    OneStepGPSVehicleBinding,
)


def _round(value, digits=1):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _iso(value):
    return value.isoformat() if value else None


def _vehicle_label(device_name, device_id):
    return (device_name or '').strip() or (device_id or '')[-8:] or 'Unknown vehicle'


def build_fleet_reports(account, days=30):
    now = timezone.now()
    since = now - timedelta(days=days)
    bindings = {
        row.device_id: (row.technician_name or '').strip()
        for row in OneStepGPSVehicleBinding.objects.filter(account=account)
    }

    trips = OneStepGPSTrip.objects.filter(account=account, started_at__gte=since)
    drives = trips.filter(kind='drive')
    stops = trips.filter(kind='stop')
    alerts = OneStepGPSAlert.objects.filter(account=account).filter(
        Q(alert_time__gte=since) | Q(alert_time__isnull=True, created_at__gte=since)
    )
    try:
        maint = list(OneStepGPSMaintenance.objects.filter(account=account).order_by('device_name'))
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        maint = []
    try:
        logs = OneStepGPSServiceLog.objects.filter(account=account, performed_at__gte=since).order_by('-performed_at')
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        logs = []
    fences = list(OneStepGPSGeofence.objects.filter(account=account).order_by('name'))

    miles = trips.aggregate(total=Sum('distance_miles')).get('total') or 0
    drive_seconds = drives.aggregate(total=Sum('duration_seconds')).get('total') or 0
    idle_seconds = trips.aggregate(total=Sum('idle_seconds')).get('total') or 0
    drive_count = drives.count()
    stop_count = stops.count()
    max_speed = trips.aggregate(total=Max('max_speed_mph')).get('total')

    vehicle_rows = list(
        trips.values('device_id', 'device_name').annotate(
            trip_count=Count('id', filter=Q(kind='drive')),
            stop_count=Count('id', filter=Q(kind='stop')),
            miles=Sum('distance_miles'),
            drive_seconds=Sum('duration_seconds', filter=Q(kind='drive')),
            idle_seconds=Sum('idle_seconds'),
            max_speed=Max('max_speed_mph'),
        ).order_by('-miles')
    )
    vehicles = []
    for row in vehicle_rows:
        device_id = row.get('device_id') or ''
        vehicles.append({
            'device_id': device_id,
            'device_name': _vehicle_label(row.get('device_name'), device_id),
            'technician_name': bindings.get(device_id) or '',
            'trips': int(row.get('trip_count') or 0),
            'stops': int(row.get('stop_count') or 0),
            'distance_miles': _round(row.get('miles') or 0),
            'drive_seconds': int(row.get('drive_seconds') or 0),
            'idle_seconds': int(row.get('idle_seconds') or 0),
            'max_speed_mph': _round(row.get('max_speed'), 0),
        })

    day_map = defaultdict(lambda: {'distance_miles': 0.0, 'trips': 0, 'stops': 0, 'idle_seconds': 0})
    daily_qs = trips.annotate(day=TruncDate('started_at')).values('day', 'kind').annotate(
        miles=Sum('distance_miles'),
        count=Count('id'),
        idle=Sum('idle_seconds'),
    )
    for row in daily_qs:
        day = row.get('day')
        if not day:
            continue
        key = day.isoformat()
        bucket = day_map[key]
        bucket['distance_miles'] += float(row.get('miles') or 0)
        bucket['idle_seconds'] += int(row.get('idle') or 0)
        if row.get('kind') == 'stop':
            bucket['stops'] += int(row.get('count') or 0)
        else:
            bucket['trips'] += int(row.get('count') or 0)

    by_day = []
    cursor = since.date()
    end_day = now.date()
    while cursor <= end_day:
        key = cursor.isoformat()
        bucket = day_map.get(key) or {}
        by_day.append({
            'date': key,
            'distance_miles': _round(bucket.get('distance_miles') or 0),
            'trips': int(bucket.get('trips') or 0),
            'stops': int(bucket.get('stops') or 0),
            'idle_seconds': int(bucket.get('idle_seconds') or 0),
        })
        cursor += timedelta(days=1)

    recent_trips = []
    for trip in trips.order_by('-started_at')[:40]:
        recent_trips.append({
            'kind': trip.kind,
            'device_name': _vehicle_label(trip.device_name, trip.device_id),
            'started_at': _iso(trip.started_at),
            'ended_at': _iso(trip.ended_at),
            'duration_seconds': int(trip.duration_seconds or 0),
            'distance_miles': _round(trip.distance_miles or 0),
            'idle_seconds': int(trip.idle_seconds or 0),
            'max_speed_mph': _round(trip.max_speed_mph, 0),
            'start_address': trip.start_address or '',
            'end_address': trip.end_address or '',
        })

    safety = alerts.filter(
        Q(alert_name__icontains='speed')
        | Q(alert_name__icontains='harsh')
        | Q(alert_name__icontains='brak')
        | Q(alert_name__icontains='accel')
        | Q(alert_name__icontains='corner')
    )
    geofence_alerts = alerts.filter(
        Q(alert_name__icontains='geofence')
        | Q(alert_name__icontains='entry')
        | Q(alert_name__icontains='exit')
        | Q(alert_name__icontains='after')
    )

    driver_rows = list(
        alerts.values('device_id', 'device_name').annotate(
            events=Count('id'),
            speeding=Count('id', filter=Q(alert_name__icontains='speed')),
            harsh=Count('id', filter=Q(alert_name__icontains='harsh') | Q(alert_name__icontains='brak')),
            max_speed=Max('speed_mph'),
        ).order_by('-events')
    )
    by_driver = []
    for row in driver_rows:
        device_id = row.get('device_id') or ''
        by_driver.append({
            'device_id': device_id,
            'device_name': _vehicle_label(row.get('device_name'), device_id),
            'technician_name': bindings.get(device_id) or '',
            'events': int(row.get('events') or 0),
            'speeding': int(row.get('speeding') or 0),
            'harsh': int(row.get('harsh') or 0),
            'max_speed_mph': _round(row.get('max_speed'), 0),
        })

    recent_alerts = []
    for alert in alerts.order_by('-alert_time', '-created_at')[:50]:
        recent_alerts.append({
            'alert_name': alert.alert_name or 'Alert',
            'device_name': _vehicle_label(alert.device_name, alert.device_id),
            'alert_time': _iso(alert.alert_time or alert.created_at),
            'speed_mph': _round(alert.speed_mph, 0),
            'posted_speed_limit_mph': _round(alert.posted_speed_limit_mph, 0),
            'severity': alert.severity,
            'acknowledged': bool(alert.acknowledged),
            'location': alert.location_raw or '',
        })
    sample = list(alerts[:400])
    critical = sum(1 for a in sample if a.severity == 'critical')
    warning = sum(1 for a in sample if a.severity == 'warning')
    info = sum(1 for a in sample if a.severity == 'info')

    due_rows = [due_info(row) for row in maint]
    vehicles_detail = []
    for row, info in zip(maint, due_rows):
        vehicles_detail.append({
            'device_name': _vehicle_label(row.device_name, row.device_id),
            'technician_name': bindings.get(row.device_id) or '',
            'odometer_miles': _round(row.odometer_miles, 0),
            'engine_hours': _round(row.engine_hours, 1),
            'fuel_level_percent': _round(row.fuel_level_percent, 0),
            'dtc_codes': row.dtc_codes or [],
            'check_engine': bool(row.check_engine),
            'due_status': info['due_status'],
            'miles_remaining': info['miles_remaining'],
            'next_service_miles': _round(row.next_service_miles, 0),
            'next_service_at': _iso(row.next_service_at),
            'last_service_at': _iso(row.last_service_at),
            'service_type': row.service_type or '',
        })

    recent_service = []
    for log in logs[:25]:
        recent_service.append({
            'device_name': _vehicle_label(log.device_name, log.device_id),
            'service_type': log.service_type or 'service',
            'performed_at': _iso(log.performed_at),
            'odometer_miles': _round(log.odometer_miles, 0),
            'notes': (log.notes or '')[:240],
        })

    zones = []
    for fence in fences:
        zones.append({
            'name': fence.name,
            'radius_miles': _round(fence.radius_miles, 2),
            'latitude': _round(fence.latitude, 5),
            'longitude': _round(fence.longitude, 5),
            'trigger_entry': bool(fence.trigger_entry),
            'trigger_exit': bool(fence.trigger_exit),
            'after_hours': bool(fence.after_hours),
            'is_active': bool(fence.is_active),
        })

    recent_geo = []
    for alert in geofence_alerts.order_by('-alert_time', '-created_at')[:40]:
        recent_geo.append({
            'alert_name': alert.alert_name or 'Geofence',
            'device_name': _vehicle_label(alert.device_name, alert.device_id),
            'alert_time': _iso(alert.alert_time or alert.created_at),
            'location': alert.location_raw or '',
        })

    company = (getattr(account, 'company_name', None) or '').strip() or 'Fleet Center'
    return {
        'period_days': days,
        'period_start': _iso(since),
        'period_end': _iso(now),
        'generated_at': _iso(now),
        'account': {
            'company_name': company,
            'location_id': getattr(account, 'location_id', None),
            'timezone': getattr(account, 'timezone', None) or 'America/Chicago',
        },
        'fleet_activity': {
            'distance_miles': _round(miles) or 0,
            'trips': drive_count,
            'stops': stop_count,
            'idle_seconds': int(idle_seconds or 0),
            'drive_seconds': int(drive_seconds or 0),
            'avg_trip_miles': _round((miles / drive_count) if drive_count else 0),
            'max_speed_mph': _round(max_speed, 0),
            'vehicles': vehicles,
            'by_day': by_day,
            'recent_trips': recent_trips,
        },
        'driver_safety': {
            'events': safety.count(),
            'speeding': safety.filter(alert_name__icontains='speed').count(),
            'harsh': safety.filter(Q(alert_name__icontains='harsh') | Q(alert_name__icontains='brak')).count(),
            'total_alerts': alerts.count(),
            'open': alerts.filter(acknowledged=False).count(),
            'acknowledged': alerts.filter(acknowledged=True).count(),
            'critical': critical,
            'warning': warning,
            'info': info,
            'by_driver': by_driver,
            'recent_alerts': recent_alerts,
        },
        'maintenance_health': {
            'vehicles': len(maint),
            'need_attention': sum(1 for info in due_rows if info['needs_attention']),
            'dtc_vehicles': sum(1 for row in maint if row.dtc_codes),
            'overdue': sum(1 for info in due_rows if info['due_status'] == 'overdue'),
            'due_soon': sum(1 for info in due_rows if info['due_status'] == 'due_soon'),
            'vehicles_detail': vehicles_detail,
            'recent_service': recent_service,
        },
        'location_activity': {
            'geofence_events': geofence_alerts.count(),
            'active_geofences': sum(1 for fence in fences if fence.is_active),
            'entry_events': geofence_alerts.filter(alert_name__icontains='entry').count(),
            'exit_events': geofence_alerts.filter(alert_name__icontains='exit').count(),
            'after_hours_events': geofence_alerts.filter(Q(alert_name__icontains='after') | Q(alert_name__icontains='hours')).count(),
            'zones': zones,
            'recent_events': recent_geo,
        },
    }
