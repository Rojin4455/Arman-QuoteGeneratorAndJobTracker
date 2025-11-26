import pytz
from django.utils import timezone as django_timezone
from service_app.models import User


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

