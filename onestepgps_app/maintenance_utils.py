from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def parse_service_datetime(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    text = str(value).strip()
    if not text:
        return None
    dt = parse_datetime(text.replace('Z', '+00:00') if text.endswith('Z') else text)
    if dt:
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    day = parse_date(text)
    if day:
        return timezone.make_aware(datetime.combine(day, time(12, 0)))
    return None


def due_info(row, now=None):
    now = now or timezone.now()
    overdue = False
    due_soon = False
    miles_remaining = None
    days_remaining = None

    if row.next_service_miles is not None and row.odometer_miles is not None:
        miles_remaining = round(float(row.next_service_miles) - float(row.odometer_miles), 1)
        if miles_remaining <= 0:
            overdue = True
        elif miles_remaining <= 500:
            due_soon = True

    if row.next_service_at:
        delta_days = (row.next_service_at - now).total_seconds() / 86400
        days_remaining = round(delta_days, 1)
        if delta_days <= 0:
            overdue = True
        elif delta_days <= 14:
            due_soon = True

    if overdue:
        status = 'overdue'
    elif due_soon:
        status = 'due_soon'
    elif row.check_engine or (row.dtc_codes or []):
        status = 'fault'
    else:
        status = 'ok'

    return {
        'due_status': status,
        'miles_remaining': miles_remaining,
        'days_remaining': days_remaining,
        'needs_attention': status in ('overdue', 'fault'),
    }


def roll_next_service(row, performed_at, odometer_miles):
    if row.interval_miles and odometer_miles is not None:
        row.next_service_miles = float(odometer_miles) + float(row.interval_miles)
    if row.interval_days and performed_at:
        row.next_service_at = performed_at + timedelta(days=int(row.interval_days))
    return row
