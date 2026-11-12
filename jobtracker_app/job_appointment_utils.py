"""
Shared logic for checking if a Job has a matching Appointment (by slot/time, calendar, location, assignee).
Uses the same manual check as slot_reserved_info: no reliance on Job.appointment relation.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

import pytz
from django.db.models import Q
from django.utils import timezone as django_timezone

from accounts.models import GHLAuthCredentials, Contact
from service_app.models import Appointment


def _get_job_slot_utc_and_location(job):
    """
    Resolve job's scheduled slot as UTC start/end and location_id (same logic as slot_reserved_info).
    Returns (job_start_time_utc, job_end_time_utc, location_id) or None if cannot resolve.
    """
    if not job.scheduled_at or not job.duration_hours:
        return None

    location_id = None
    credentials = None
    try:
        if job.submission and hasattr(job.submission, 'contact') and job.submission.contact:
            location_id = job.submission.contact.location_id
            credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if not location_id or not credentials:
            credentials = GHLAuthCredentials.objects.first()
            if credentials:
                location_id = credentials.location_id or location_id
    except Exception:
        credentials = GHLAuthCredentials.objects.first()
        if credentials:
            location_id = credentials.location_id

    if not location_id or not credentials:
        return None

    from accounts.timezone_utils import get_pytz_timezone

    timezone_str = credentials.timezone if credentials.timezone else "America/Chicago"
    tz = get_pytz_timezone(timezone_str)

    try:
        job_start_time = job.scheduled_at
        if django_timezone.is_naive(job_start_time):
            job_start_time = tz.localize(job_start_time)
        else:
            naive_time = job_start_time.replace(tzinfo=None)
            job_start_time = tz.localize(naive_time)
        duration_hours = float(job.duration_hours)
        job_end_time = job_start_time + timedelta(hours=duration_hours)
        job_start_time_utc = job_start_time.astimezone(pytz.UTC)
        job_end_time_utc = job_end_time.astimezone(pytz.UTC)
        return (job_start_time_utc, job_end_time_utc, location_id)
    except (ValueError, TypeError, Exception):
        return None


def job_has_matching_appointment(job):
    """
    Check if any assignee of this job already has an Appointment matching the job's slot
    (same start/end time, calendar "Reccuring Service Calendar", location_id, assigned_user).
    Does not use job.appointment relation.
    """
    slot = _get_job_slot_utc_and_location(job)
    if not slot:
        return False
    job_start_utc, job_end_utc, location_id = slot

    for assignment in job.assignments.select_related('user').all():
        if not assignment.user:
            continue
        exists = Appointment.objects.filter(
            start_time=job_start_utc,
            end_time=job_end_utc,
            calendar__name="Reccuring Service Calendar",
            location_id=location_id,
            assigned_user=assignment.user,
        ).filter(appointment_contact_q(job)).exists()
        if exists:
            return True
    return False


def get_assignee_ghl_ids_without_matching_appointment(job):
    """
    Return list of assigned user GHL IDs for which there is NO matching Appointment
    (same slot, calendar, location). Create in GHL only for these assignees.
    If slot cannot be resolved, returns all assignee GHL IDs (create for everyone).
    """
    all_ghl_ids = []
    for assignment in job.assignments.select_related('user').all():
        if assignment.user and assignment.user.ghl_user_id:
            all_ghl_ids.append(assignment.user.ghl_user_id)

    slot = _get_job_slot_utc_and_location(job)
    if not slot:
        return all_ghl_ids

    job_start_utc, job_end_utc, location_id = slot
    without = []
    for assignment in job.assignments.select_related('user').all():
        if not assignment.user or not assignment.user.ghl_user_id:
            continue
        exists = Appointment.objects.filter(
            start_time=job_start_utc,
            end_time=job_end_utc,
            calendar__name="Reccuring Service Calendar",
            location_id=location_id,
            assigned_user=assignment.user,
        ).filter(appointment_contact_q(job)).exists()
        if not exists:
            without.append(assignment.user.ghl_user_id)
    return without


def _job_contact_dict(contact):
    """Build contact dict for API response."""
    if not contact:
        return None
    name = f"{getattr(contact, 'first_name', '') or ''} {getattr(contact, 'last_name', '') or ''}".strip()
    return {
        'id': getattr(contact, 'contact_id', None),
        'name': name or getattr(contact, 'name', None),
        'email': getattr(contact, 'email', None),
    }


def _resolve_job_contact(job):
    """
    Resolve a Contact instance for the job for appointment matching.
    Tries in order: job.contact FK, submission.contact, then Contact table by
    job.ghl_contact_id, then by job.customer_email.
    """
    contact = getattr(job, 'contact', None)
    if contact:
        return contact
    if getattr(job, 'submission_id', None):
        try:
            submission = getattr(job, 'submission', None)
            if submission and getattr(submission, 'contact', None):
                return submission.contact
        except Exception:
            pass
    ghl_id = getattr(job, 'ghl_contact_id', None)
    if ghl_id:
        contact = Contact.objects.filter(contact_id=ghl_id).first()
        if contact:
            return contact
    email = getattr(job, 'customer_email', None)
    if email:
        contact = Contact.objects.filter(email__iexact=email).first()
        if contact:
            return contact
    return None


def job_contact_ghl_id(job):
    """GHL contact id of the job's customer, or None if it cannot be resolved."""
    ghl_id = getattr(job, 'ghl_contact_id', None)
    if ghl_id:
        return ghl_id
    contact = _resolve_job_contact(job)
    return getattr(contact, 'contact_id', None) if contact else None


def appointment_contact_q(job):
    """
    Q limiting Appointment rows to those that belong to the job's contact.
    Slot/assignee alone is NOT enough to say an appointment belongs to a job:
    two different customers can share a technician and time.
    If the job's contact is unknown, only contact-less appointments match.
    """
    ghl_id = job_contact_ghl_id(job)
    if ghl_id:
        return Q(ghl_contact_id=ghl_id) | Q(contact__contact_id=ghl_id)
    return Q(ghl_contact_id__isnull=True) & Q(contact__isnull=True)


def appointment_belongs_to_job_contact(appointment, job):
    """True if the appointment's contact is the same as the job's contact."""
    ghl_id = job_contact_ghl_id(job)
    appt_ghl_id = appointment.ghl_contact_id or (
        appointment.contact.contact_id if appointment.contact_id else None
    )
    return (ghl_id or None) == (appt_ghl_id or None)


def get_slot_reserved_info_for_job(job):
    """
    Same manual check as JobSerializer.get_slot_reserved_info: returns slot_reserved and
    appointment details if any assignee has a matching appointment (optionally filtered by
    job contact), else slot_reserved=False. Includes job_contact when available.
    Contact is resolved from job.contact, submission.contact, or Contact table by
    ghl_contact_id / customer_email.
    """
    slot = _get_job_slot_utc_and_location(job)
    if not slot:
        return None
    job_start_utc, job_end_utc, location_id = slot

    job_contact = _resolve_job_contact(job)

    base_filter = {
        'start_time': job_start_utc,
        'end_time': job_end_utc,
        'calendar__name': 'Reccuring Service Calendar',
        'location_id': location_id,
    }
    for assignment in job.assignments.select_related('user').all():
        if not assignment.user:
            continue
        try:
            appointment = Appointment.objects.filter(
                **base_filter,
                assigned_user=assignment.user,
            ).filter(appointment_contact_q(job)).select_related(
                'calendar', 'assigned_user', 'contact'
            ).first()

            if appointment:
                result = {
                    'slot_reserved': True,
                    'appointment': {
                        'id': str(appointment.id),
                        'ghl_appointment_id': appointment.ghl_appointment_id,
                        'title': appointment.title,
                        'start_time': appointment.start_time.isoformat() if appointment.start_time else None,
                        'end_time': appointment.end_time.isoformat() if appointment.end_time else None,
                        'appointment_status': appointment.appointment_status,
                        'calendar_id': appointment.calendar.ghl_calendar_id if appointment.calendar else None,
                        'calendar_name': appointment.calendar.name if appointment.calendar else None,
                        'assigned_user': {
                            'id': str(appointment.assigned_user.id),
                            'name': appointment.assigned_user.get_full_name() or appointment.assigned_user.username,
                            'email': appointment.assigned_user.email,
                        } if appointment.assigned_user else None,
                        'contact': _job_contact_dict(appointment.contact),
                        'notes': appointment.notes,
                        'address': appointment.address,
                    },
                }
                if job_contact is not None:
                    result['job_contact'] = _job_contact_dict(job_contact)
                return result
        except Exception:
            continue

    out = {'slot_reserved': False, 'appointment': None}
    if job_contact is not None:
        out['job_contact'] = _job_contact_dict(job_contact)
    return out


# Job statuses that occupy a technician's time.
# Only jobs that actually hold the technician's time. 'service_due' is just "due for
# service" (not a booked visit) and pending/completed/cancelled don't block either.
_BUSY_JOB_STATUSES = ('confirmed', 'on_the_way', 'in_progress')
_INACTIVE_APPOINTMENT_STATUSES = ('cancelled', 'invalid')


def _ghl_occurrence_is_inactive(appt):
    """True if GHL says this appointment is cancelled/deleted; also fixes our stale row."""
    try:
        import requests
        from jobtracker_app.ghl_appointment_sync import (
            get_ghl_headers, get_ghl_credentials_for_appointment,
        )
        creds = get_ghl_credentials_for_appointment(appt)
        if not creds:
            return False
        resp = requests.get(
            f'https://services.leadconnectorhq.com/calendars/events/appointments/{appt.ghl_appointment_id}',
            headers=get_ghl_headers(creds.access_token),
            timeout=8,
        )
        if resp.status_code == 404:
            status = 'cancelled'
        elif resp.ok:
            data = resp.json().get('appointment') or {}
            status = (data.get('appointmentStatus') or data.get('state') or '').lower()
        else:
            return False
        if status in ('cancelled', 'canceled', 'invalid', 'deleted'):
            Appointment.objects.filter(pk=appt.pk).update(appointment_status='cancelled')
            return True
    except Exception as exc:
        print(f"⚠️ [CONFLICT CHECK] Could not verify GHL status for appointment {appt.pk}: {exc}")
    return False


def find_assignee_conflicts(*, job, scheduled_at, duration_hours, users, ghl_contact_id=None):
    """
    Return human-readable messages for technicians in `users` who are already booked
    during [scheduled_at, scheduled_at + duration_hours].

    Checks other active Jobs and existing GHL Appointments (any calendar). An existing
    Appointment that belongs to this job, or to the same contact as this job, is not a
    conflict: that is the job's own calendar entry.
    `job` may be None (new job) or the saved instance (excluded from the check).
    """
    from types import SimpleNamespace

    from jobtracker_app.models import Job
    from accounts.timezone_utils import get_pytz_timezone

    users = [u for u in users if u]
    if not users or not scheduled_at or not duration_hours:
        return []

    probe = SimpleNamespace(
        scheduled_at=scheduled_at,
        duration_hours=duration_hours,
        submission=getattr(job, 'submission', None) if job is not None else None,
    )
    slot = _get_job_slot_utc_and_location(probe)
    if not slot:
        return []
    start_utc, end_utc, location_id = slot

    credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
    tz = get_pytz_timezone((credentials.timezone if credentials and credentials.timezone else None) or "America/Chicago")

    def fmt(start, end):
        s, e = start.astimezone(tz), end.astimezone(tz)
        return f"{s:%b} {s.day}, {s:%-I:%M %p} - {e:%-I:%M %p}"

    def user_name(u):
        return u.get_full_name() or u.username

    messages = []
    seen_user_slots = set()

    # --- other jobs (scheduled_at is stored as account-local wall time) ---
    new_start = scheduled_at.replace(tzinfo=None)
    new_end = new_start + timedelta(hours=float(duration_hours))
    job_qs = Job.objects.filter(
        assignments__user__in=users,
        status__in=_BUSY_JOB_STATUSES,
        scheduled_at__gte=scheduled_at - timedelta(days=2),
        scheduled_at__lte=scheduled_at + timedelta(days=1, hours=float(duration_hours)),
    ).distinct()
    if job is not None and getattr(job, 'pk', None):
        job_qs = job_qs.exclude(pk=job.pk)
    conflicting_job_ids = set()
    for other in job_qs.prefetch_related('assignments__user'):
        if not other.scheduled_at:
            continue
        o_start = other.scheduled_at.replace(tzinfo=None)
        o_end = o_start + timedelta(hours=float(other.duration_hours or 1))
        if not (o_start < new_end and o_end > new_start):
            continue
        shared = [a.user for a in other.assignments.all() if a.user in users]
        for u in shared:
            conflicting_job_ids.add(other.pk)
            seen_user_slots.add((u.pk, start_utc))
            messages.append(
                f'{user_name(u)} is already booked on job "{other.title or other.customer_name or other.pk}" '
                f'({o_start:%b} {o_start.day}, {o_start:%-I:%M %p} - {o_end:%-I:%M %p}).'
            )

    # --- existing appointments for the same technician ---
    appt_qs = Appointment.objects.filter(
        assigned_user__in=users,
        start_time__lt=end_utc,
        end_time__gt=start_utc,
    ).exclude(appointment_status__in=_INACTIVE_APPOINTMENT_STATUSES).exclude(
        job__status__in=('completed', 'cancelled')  # work already done / cancelled
    ).select_related('assigned_user')
    if job is not None and getattr(job, 'pk', None):
        appt_qs = appt_qs.exclude(job_id=job.pk)
    for appt in appt_qs:
        if appt.job_id and appt.job_id in conflicting_job_ids:
            continue
        appt_ghl = appt.ghl_contact_id or (appt.contact.contact_id if appt.contact_id else None)
        if ghl_contact_id and appt_ghl == ghl_contact_id:
            continue
        # Recurring occurrence ids are "<id>_<startMs>_<durationSec>"; the DB row's
        # times can be stale, so trust the id for the real occurrence time.
        a_start, a_end = appt.start_time, appt.end_time
        parts = (appt.ghl_appointment_id or '').split('_')
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            a_start = datetime.fromtimestamp(int(parts[1]) / 1000, tz=dt_timezone.utc)
            a_end = a_start + timedelta(seconds=int(parts[2]))
            if not (a_start < end_utc and a_end > start_utc):
                continue
            # Recurring occurrences cancelled/deleted in GHL often never reach our DB,
            # so confirm the live status. On any error, keep it as a conflict (safe side).
            if _ghl_occurrence_is_inactive(appt):
                continue
        messages.append(
            f'{user_name(appt.assigned_user)} already has appointment "{appt.title or "Untitled"}" '
            f'({fmt(a_start, a_end)}).'
        )
    return messages
