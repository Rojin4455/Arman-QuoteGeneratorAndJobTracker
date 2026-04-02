import base64
import binascii
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

# Known One Step GPS webhook JSON keys (many contain spaces).
WEBHOOK_FIELD_KEYS = (
    'Alert ID',
    'Alert Name',
    'Alert Time',
    'Device ID',
    'Device Name',
    'Device Point',
    'Drive Status',
    'Drive Status Duration (seconds)',
    'External Voltage',
    'Ignition On',
    'Location (Lat,Lng)',
    'Odometer',
    'Posted Speed Limit (MPH)',
    'speed',
    'Speed (MPH)',
)


def _verify_onestepgps_basic_auth(request):
    """
    Validate Basic credentials from Authorization or Authentication header
    (docs sometimes use the non-standard Authentication header name).
    """
    expected_user = (getattr(settings, 'ONESTEPGPS_WEBHOOK_USERNAME', None) or '').strip()
    expected_pass = getattr(settings, 'ONESTEPGPS_WEBHOOK_PASSWORD', None) or ''
    if not expected_user and not expected_pass:
        logger.warning(
            'ONESTEPGPS_WEBHOOK_USERNAME/PASSWORD not set; webhook accepts POST without Basic auth'
        )
        return True

    raw = request.META.get('HTTP_AUTHORIZATION') or request.META.get('HTTP_AUTHENTICATION')
    if not raw:
        return False
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'basic':
        return False
    try:
        decoded = base64.b64decode(parts[1].strip()).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    if ':' not in decoded:
        return False
    username, _, password = decoded.partition(':')
    return username == expected_user and password == expected_pass


def _normalize_webhook_payload(data):
    if not isinstance(data, dict):
        return {}
    return {k: data.get(k) for k in WEBHOOK_FIELD_KEYS}


@method_decorator(csrf_exempt, name='dispatch')
class OneStepGPSView(APIView):
    """
    One Step GPS JSON webhook. Uses HTTP Basic auth when
    ONESTEPGPS_WEBHOOK_USERNAME / ONESTEPGPS_WEBHOOK_PASSWORD are set.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok', 'service': 'onestepgps-webhook'})

    def post(self, request):
        if not _verify_onestepgps_basic_auth(request):
            return Response(
                {'detail': 'Unauthorized'},
                status=401,
                headers={'WWW-Authenticate': 'Basic realm="OneStepGPS"'},
            )

        payload = request.data if isinstance(request.data, dict) else {}

        alert_id = payload.get('Alert ID')
        if alert_id is not None and str(alert_id).strip() != '':
            cache_key = f'onestepgps:webhook:{alert_id}'
            if cache.get(cache_key):
                logger.info('OneStepGPS duplicate webhook ignored alert_id=%s', alert_id)
                return Response({'status': 'ok', 'duplicate': True}, status=200)
            cache.set(cache_key, True, timeout=60 * 60 * 72)

        normalized = _normalize_webhook_payload(payload)
        logger.info('OneStepGPS webhook received: %s', normalized)

        return Response({'status': 'ok', 'received': True}, status=200)
