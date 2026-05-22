"""
Helpers for flexible employee time off (full day, half day, custom hours).
Times are interpreted in the employee's profile timezone (see get_user_timezone).
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

STANDARD_WORK_HOURS = Decimal('8')
COVERAGE_VALUES = frozenset({
    'full_day',
    'half_day_am',
    'half_day_pm',
    'custom',
})
NOON = time(12, 0)
END_OF_DAY = time(23, 59, 59)


def coverage_fraction(coverage):
    """Fraction of a standard work day (0–1) for one calendar day."""
    if coverage == 'full_day':
        return Decimal('1')
    if coverage in ('half_day_am', 'half_day_pm'):
        return Decimal('0.5')
    return None


def coverage_to_time_range(coverage, start_time=None, end_time=None):
    """
    Return (start_time, end_time) for one calendar day in local time.
    For custom coverage, start_time and end_time are required.
    """
    if coverage == 'full_day':
        return time(0, 0), END_OF_DAY
    if coverage == 'half_day_am':
        return time(0, 0), NOON
    if coverage == 'half_day_pm':
        return time(12, 0), END_OF_DAY
    if coverage == 'custom':
        if not start_time or not end_time:
            raise ValueError('Custom coverage requires start and end times.')
        if end_time <= start_time:
            raise ValueError('End time must be after start time.')
        return start_time, end_time
    raise ValueError(f'Unknown coverage: {coverage}')


def _combine(day, t):
    return datetime.combine(day, t)


def time_ranges_overlap(day, range_a, range_b):
    """True if two (start_time, end_time) pairs overlap on the same calendar day."""
    a_start, a_end = range_a
    b_start, b_end = range_b
    a0 = _combine(day, a_start)
    a1 = _combine(day, a_end)
    b0 = _combine(day, b_start)
    b1 = _combine(day, b_end)
    return a0 < b1 and b0 < a1


def custom_hours_fraction(start_time, end_time):
    """Hours off / standard work day, capped at 1."""
    delta = datetime.combine(date.min, end_time) - datetime.combine(
        date.min, start_time
    )
    hours = Decimal(str(delta.total_seconds() / 3600))
    fraction = (hours / STANDARD_WORK_HOURS).quantize(Decimal('0.01'))
    return min(fraction, Decimal('1'))


def day_fraction(coverage, start_time=None, end_time=None):
    frac = coverage_fraction(coverage)
    if frac is not None:
        return frac
    return custom_hours_fraction(start_time, end_time)


def iter_day_specs(entry):
    """
    Yield (calendar_date, coverage, custom_start, custom_end) for each day in entry.
    """
    if entry.start_date > entry.end_date:
        return

    if entry.start_date == entry.end_date:
        yield (
            entry.start_date,
            entry.coverage,
            entry.start_time,
            entry.end_time,
        )
        return

    current = entry.start_date
    while current <= entry.end_date:
        if current == entry.start_date:
            yield (
                current,
                entry.start_day_coverage,
                entry.start_time,
                entry.end_time,
            )
        elif current == entry.end_date:
            yield (
                current,
                entry.end_day_coverage,
                entry.end_start_time,
                entry.end_end_time,
            )
        else:
            yield (current, 'full_day', None, None)
        current += timedelta(days=1)


def equivalent_days(entry):
    """Total time off expressed as full-day equivalents (e.g. 0.5, 2.5)."""
    total = Decimal('0')
    for _day, coverage, c_start, c_end in iter_day_specs(entry):
        total += day_fraction(coverage, c_start, c_end)
    return total.quantize(Decimal('0.01'))


def entry_covers_date(entry, check_date, check_start=None, check_end=None):
    """
    True if this time off blocks the employee on check_date.
    If check_start/check_end are provided (time objects), only block when
    the off interval overlaps that window; otherwise any overlap on that day counts.
    """
    if not (entry.start_date <= check_date <= entry.end_date):
        return False

    for day, coverage, c_start, c_end in iter_day_specs(entry):
        if day != check_date:
            continue
        off_range = coverage_to_time_range(coverage, c_start, c_end)
        if check_start is None and check_end is None:
            return True
        query_range = (check_start, check_end)
        return time_ranges_overlap(check_date, off_range, query_range)
    return False


def entry_overlaps_date_range(entry, range_start, range_end):
    """True if entry overlaps an inclusive calendar date range (any amount)."""
    if entry.end_date < range_start or entry.start_date > range_end:
        return False
    if entry.start_date == entry.end_date:
        cov = entry.coverage
        if cov == 'full_day':
            return True
        if cov in ('half_day_am', 'half_day_pm', 'custom'):
            return True
    # Multi-day: at least one day in overlap; partial boundaries still overlap.
    overlap_start = max(entry.start_date, range_start)
    overlap_end = min(entry.end_date, range_end)
    return overlap_start <= overlap_end


def entries_blocking_date(entries, check_date, check_start=None, check_end=None):
    """Employee IDs (or entries) that are off on check_date for the optional time window."""
    blocking = []
    for entry in entries:
        if entry_covers_date(entry, check_date, check_start, check_end):
            blocking.append(entry)
    return blocking


def validate_time_off_entry(entry):
    """
    Return dict of field -> message for model/serializer validation.
    `entry` may be unsaved (no pk).
    """
    errors = {}
    valid_cov = COVERAGE_VALUES

    if entry.start_date and entry.end_date and entry.end_date < entry.start_date:
        errors['end_date'] = 'End date must be on or after start date.'
        return errors

    def check_coverage(name, value):
        if value not in valid_cov:
            errors[name] = f'Invalid coverage. Choose one of: {", ".join(sorted(valid_cov))}.'

    def check_custom_times(cov, t_start, t_end, prefix):
        if cov != 'custom':
            if t_start or t_end:
                errors[prefix] = 'Times are only allowed when coverage is custom.'
            return
        if not t_start or not t_end:
            errors[prefix] = 'Start and end times are required for custom coverage.'
            return
        if t_end <= t_start:
            errors[prefix] = 'End time must be after start time.'

    if entry.is_single_day:
        check_coverage('coverage', entry.coverage or 'full_day')
        check_custom_times(
            entry.coverage,
            entry.start_time,
            entry.end_time,
            'start_time',
        )
        if entry.start_day_coverage != 'full_day' or entry.end_day_coverage != 'full_day':
            errors['start_day_coverage'] = (
                'Use coverage for single-day entries; leave start/end day coverage as full_day.'
            )
        if entry.end_start_time or entry.end_end_time:
            errors['end_start_time'] = 'End-day times are only for multi-day ranges.'
    else:
        check_coverage('start_day_coverage', entry.start_day_coverage or 'full_day')
        check_coverage('end_day_coverage', entry.end_day_coverage or 'full_day')
        check_custom_times(
            entry.start_day_coverage,
            entry.start_time,
            entry.end_time,
            'start_time',
        )
        check_custom_times(
            entry.end_day_coverage,
            entry.end_start_time,
            entry.end_end_time,
            'end_start_time',
        )
        if entry.coverage != 'full_day':
            errors['coverage'] = (
                'Use start_day_coverage and end_day_coverage for multi-day ranges.'
            )

    if not errors:
        try:
            for _day, coverage, c_start, c_end in iter_day_specs(entry):
                coverage_to_time_range(coverage, c_start, c_end)
        except ValueError as exc:
            errors['coverage'] = str(exc)

    return errors


def parse_time_param(value, field_name):
    """Parse HH:MM or HH:MM:SS from query string; raises ValueError."""
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f'{field_name} must be a valid time (HH:MM or HH:MM:SS).')


def parse_period_param(period):
    """
    Map period=am|pm to a check window on a calendar day.
    AM: 00:00–12:00, PM: 12:00–23:59:59.
    """
    p = (period or '').strip().lower()
    if p == 'am':
        return time(0, 0), NOON
    if p == 'pm':
        return time(12, 0), END_OF_DAY
    raise ValueError('period must be am or pm.')


def queryset_overlapping_dates(queryset, from_date, to_date):
    """Entries overlapping inclusive calendar range (any coverage)."""
    return queryset.filter(start_date__lte=to_date, end_date__gte=from_date)


def employee_ids_off_in_window(queryset, range_start, range_end, check_start=None, check_end=None):
    """
    Distinct employee IDs unavailable for the whole date range, or for the
    optional daily time window applied to each day in the range.
    """
    if check_start is None and check_end is None:
        return queryset.filter(
            start_date__lte=range_end,
            end_date__gte=range_start,
        ).values_list('employee_id', flat=True).distinct()

    off_ids = set()
    candidates = queryset.filter(
        start_date__lte=range_end,
        end_date__gte=range_start,
    ).select_related('employee')
    current = range_start
    while current <= range_end:
        for entry in candidates:
            if entry.employee_id in off_ids:
                continue
            if entry_covers_date(entry, current, check_start, check_end):
                off_ids.add(entry.employee_id)
        current += timedelta(days=1)
    return off_ids
