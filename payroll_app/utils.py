import pytz
from django.utils import timezone as django_timezone
from service_app.models import User


def is_first_time_bonus_eligible(job):
    """
    Whether the quoted-by person should receive the first-time bonus rate.

    - one_time: always uses first_time_bonus_percentage
    - recurring: first_time_bonus_percentage only on the first *completed* job in
      the series (by series_id); later completions use quoted_by_bonus_percentage
    """
    if job.job_type == 'one_time':
        return True
    if job.job_type != 'recurring':
        return False

    series_id = getattr(job, 'series_id', None)
    if not series_id:
        return True

    from jobtracker_app.models import Job

    completed_in_series = Job.objects.filter(
        series_id=series_id,
        status='completed',
    ).exclude(pk=job.pk)
    job_account_id = getattr(job, 'account_id', None)
    if job_account_id:
        completed_in_series = completed_in_series.filter(account_id=job_account_id)
    return not completed_in_series.exists()


def get_user_timezone(user):
    """Get user's timezone from employee profile"""
    try:
        return user.employee_profile.timezone
    except:
        return 'UTC'


def convert_utc_to_user_timezone(utc_datetime, user):
    """Convert UTC datetime to user's local timezone"""
    try:
        user_tz = get_user_timezone(user)
        tz = pytz.timezone(user_tz)
        # Ensure datetime is timezone-aware (UTC)
        if django_timezone.is_naive(utc_datetime):
            utc_datetime = django_timezone.make_aware(utc_datetime, pytz.UTC)
        # Convert to user's timezone
        return utc_datetime.astimezone(tz)
    except Exception:
        # Fallback to UTC
        if django_timezone.is_naive(utc_datetime):
            return django_timezone.make_aware(utc_datetime, pytz.UTC)
        return utc_datetime


def ensure_utc(datetime_obj):
    """Ensure datetime is timezone-aware and in UTC"""
    if django_timezone.is_naive(datetime_obj):
        return django_timezone.make_aware(datetime_obj, pytz.UTC)
    return datetime_obj.astimezone(pytz.UTC)

