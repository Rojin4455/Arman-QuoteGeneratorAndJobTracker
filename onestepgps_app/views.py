import base64
import binascii
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import AccountScopedPermission, IsAdminPermission, IsManagementUserPermission

from .alert_store import normalize_webhook_payload, persist_webhook_alert
from .models import OneStepGPSAlert, OneStepGPSIntegration
from .serializers import OneStepGPSAlertSerializer, OneStepGPSIntegrationSerializer
from .services import OneStepGPSAPIError, fetch_devices_from_api, test_api_key

logger = logging.getLogger(__name__)


def _webhook_credential_pairs(integration=None):
    """(source, username, password) from the account row and/or .env."""
    pairs = []
    if integration is not None:
        user = (integration.webhook_username or '').strip()
        password = integration.webhook_password or ''
        if user or password:
            pairs.append(('integration', user, password))
    env_user = (getattr(settings, 'ONESTEPGPS_WEBHOOK_USERNAME', None) or '').strip()
    env_pass = getattr(settings, 'ONESTEPGPS_WEBHOOK_PASSWORD', None) or ''
    if env_user or env_pass:
        pairs.append(('env', env_user, env_pass))
    return pairs


def _decode_basic_auth(header: str) -> tuple[str, str] | None:
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'basic':
        return None
    try:
        decoded = base64.b64decode(parts[1].strip()).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ':' not in decoded:
        return None
    username, _, password = decoded.partition(':')
    return username, password


def _verify_webhook_auth(request, integration=None, *, location_scoped=False):
    """
    OneStep JSON webhooks send HTTP Basic (Go client). DataQueue may send Bearer.

    Accept if the incoming header matches either:
    - per-subaccount username/password saved in GPS settings
    - global ONESTEPGPS_WEBHOOK_USERNAME / ONESTEPGPS_WEBHOOK_PASSWORD

    Location-scoped URL already identifies the tenant. If OneStep sends no
    Authorization header (common) or nginx dropped it, accept the POST so
    alerts are not discarded.
    """
    auth_header = (
        request.META.get('HTTP_AUTHORIZATION')
        or request.META.get('HTTP_AUTHENTICATION')
        or ''
    ).strip()
    pairs = _webhook_credential_pairs(integration)

    if not auth_header:
        if location_scoped:
            logger.warning(
                'OneStepGPS webhook accepted without Authorization header (location-scoped URL)'
            )
            return True
        if not pairs:
            return True
        logger.warning('OneStepGPS webhook rejected: missing Authorization header')
        return False

    scheme = auth_header.split(None, 1)[0].lower() if auth_header else ''

    if scheme == 'bearer':
        token = auth_header[7:].strip()
        for source, _user, password in pairs:
            if password and token == password:
                return True
        logger.warning('OneStepGPS webhook rejected: Bearer token mismatch sources=%s', [p[0] for p in pairs])
        return False

    if scheme == 'basic':
        decoded = _decode_basic_auth(auth_header)
        if decoded is None:
            logger.warning('OneStepGPS webhook rejected: invalid Basic header')
            return False
        username, password = decoded
        for source, expected_user, expected_pass in pairs:
            if username == expected_user and password == expected_pass:
                return True
        logger.warning(
            'OneStepGPS webhook rejected: Basic user mismatch sources=%s incoming_user=%s',
            [p[0] for p in pairs],
            username,
        )
        return False

    logger.warning('OneStepGPS webhook rejected: unsupported auth scheme=%s', scheme)
    return False


def _parse_webhook_payload(request):
    data = request.data
    if isinstance(data, (dict, list)):
        return data
    raw = getattr(request, 'body', b'') or b''
    if not raw:
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _dataqueue_payload_preview(payload):
    items = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])
    schemas = []
    first_keys = []
    first_value_keys = []
    for item in items[:8]:
        if not isinstance(item, dict):
            schemas.append(type(item).__name__)
            continue
        schema = item.get('schema') or item.get('event_type') or item.get('type') or 'object'
        schemas.append(str(schema))
        if not first_keys:
            first_keys = list(item.keys())[:12]
            value = item.get('value')
            if isinstance(value, dict):
                first_value_keys = list(value.keys())[:12]
    return {
        'count': len(items) if isinstance(payload, list) else (1 if payload not in (None, '', {}, []) else 0),
        'payload_type': type(payload).__name__,
        'schemas': schemas,
        'first_keys': first_keys,
        'first_value_keys': first_value_keys,
    }


class OneStepGPSIntegrationView(APIView):
    """Get or update One Step GPS settings for the current account."""

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AccountScopedPermission(), IsManagementUserPermission()]
        return [AccountScopedPermission(), IsAdminPermission()]

    def get(self, request):
        integration = OneStepGPSIntegration.get_for_account(request.account)
        serializer = OneStepGPSIntegrationSerializer(integration, context={'request': request})
        return Response(serializer.data)

    def patch(self, request):
        integration = OneStepGPSIntegration.get_for_account(request.account)
        serializer = OneStepGPSIntegrationSerializer(
            integration,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(OneStepGPSIntegrationSerializer(integration, context={'request': request}).data)


class OneStepGPSTestConnectionView(APIView):
    """Validate the stored or submitted API key against One Step GPS."""

    permission_classes = [AccountScopedPermission, IsAdminPermission]

    def post(self, request):
        integration = OneStepGPSIntegration.get_for_account(request.account)
        api_key = (request.data.get('api_key') or '').strip() or (integration.api_key or '').strip()
        if not api_key:
            return Response({'detail': 'API key is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = test_api_key(api_key)
        except OneStepGPSAPIError as exc:
            return Response({'ok': False, 'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class OneStepGPSDevicesView(APIView):
    """Return live device positions for the current account."""

    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        integration = OneStepGPSIntegration.get_for_account(request.account)
        if not integration or not integration.is_enabled or not integration.api_key_configured:
            return Response({'devices': [], 'configured': False})

        cache_key = f'onestepgps:devices:{request.account.pk}'
        try:
            devices = fetch_devices_from_api(
                integration.api_key,
                cache_key=cache_key,
            )
        except OneStepGPSAPIError as exc:
            logger.warning('OneStepGPS device fetch failed account=%s: %s', request.account.pk, exc)
            return Response(
                {'detail': str(exc), 'configured': True, 'devices': []},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        from .fleet_store import upsert_maintenance_from_devices
        from .models import OneStepGPSVehicleBinding

        upsert_maintenance_from_devices(request.account, devices)
        bindings = {
            b.device_id: b
            for b in OneStepGPSVehicleBinding.objects.filter(account=request.account)
        }
        for device in devices:
            binding = bindings.get(device.get('device_id'))
            if binding:
                device['technician_id'] = binding.user_id
                device['technician_name'] = binding.technician_name or (
                    binding.user.get_full_name() if binding.user else ''
                )
            else:
                device['technician_id'] = None
                device['technician_name'] = ''

        return Response({'devices': devices, 'configured': True})


class OneStepGPSAlertsRecentView(APIView):
    """Chronological alert feed (Recent tab)."""

    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get('limit', 100)), 300)
        except (TypeError, ValueError):
            limit = 100

        search = (request.query_params.get('search') or '').strip()
        qs = OneStepGPSAlert.objects.filter(account=request.account)
        if search:
            qs = qs.filter(
                Q(alert_name__icontains=search)
                | Q(device_name__icontains=search)
                | Q(device_id__icontains=search)
            )

        alerts = qs.order_by(Coalesce('alert_time', 'created_at').desc())[:limit]
        return Response({
            'results': OneStepGPSAlertSerializer(alerts, many=True).data,
            'count': qs.count(),
        })


class OneStepGPSAlertsCountsView(APIView):
    """Aggregated alert counts by name (Counts tab)."""

    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        days = request.query_params.get('days')
        qs = OneStepGPSAlert.objects.filter(account=request.account)

        if days not in (None, '', 'all'):
            try:
                day_count = max(1, min(int(days), 365))
                since = timezone.now() - timedelta(days=day_count)
                qs = qs.filter(
                    Q(alert_time__gte=since) | Q(alert_time__isnull=True, created_at__gte=since)
                )
            except (TypeError, ValueError):
                pass

        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(alert_name__icontains=search)

        rows = (
            qs.values('alert_name')
            .annotate(
                count=Count('id'),
                last_event_at=Max(Coalesce('alert_time', 'created_at')),
            )
            .order_by('-count', 'alert_name')
        )

        results = [
            {
                'alert_name': row['alert_name'] or 'Alert',
                'count': row['count'],
                'last_event_at': row['last_event_at'],
            }
            for row in rows
        ]
        return Response({'results': results})


@method_decorator(csrf_exempt, name='dispatch')
class OneStepGPSView(APIView):
    """
    One Step GPS JSON webhook. Uses HTTP Basic auth when credentials are set
    globally or on the account integration record.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, location_id=None):
        return Response({'status': 'ok', 'service': 'onestepgps-webhook', 'location_id': location_id})

    def post(self, request, location_id=None):
        from accounts.models import GHLAuthCredentials

        account = None
        integration = None

        if location_id:
            account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
            if account is None:
                logger.warning('OneStepGPS webhook unknown location_id=%s', location_id)
                return Response({'detail': 'Unknown location.'}, status=404)
            integration = OneStepGPSIntegration.get_for_account(account)

        if not _verify_webhook_auth(
            request,
            integration=integration,
            location_scoped=bool(location_id),
        ):
            return Response(
                {'detail': 'Unauthorized'},
                status=401,
                headers={'WWW-Authenticate': 'Basic realm="OneStepGPS"'},
            )

        payload = _parse_webhook_payload(request)
        if account is not None and integration is not None:
            integration.last_webhook_at = timezone.now()
            integration.save(update_fields=['last_webhook_at'])

        from .bundle_store import persist_operational_bundle
        from .dataqueue import extract_dataqueue_bundle, is_batch_payload
        from .fleet_store import persist_webhook_fleet_events

        if account is not None and is_batch_payload(payload):
            bundle = extract_dataqueue_bundle(payload)
            stats = persist_operational_bundle(account, bundle)
            logger.info(
                'OneStepGPS DataQueue location_id=%s events=%s alerts=%s trips=%s maintenance=%s',
                location_id,
                stats.get('events'),
                stats.get('alerts'),
                stats.get('trips'),
                stats.get('maintenance'),
            )
            return Response(
                {
                    'status': 'ok',
                    'accepted': True,
                    'processed': stats.get('events', 0),
                    'normalized': {
                        'alerts': stats.get('alerts', 0),
                        'trips': stats.get('trips', 0),
                        'maintenance': stats.get('maintenance', 0),
                        'vehicles': stats.get('vehicles', 0),
                    },
                },
                status=202,
            )

        if not isinstance(payload, dict):
            logger.warning('OneStepGPS webhook unsupported payload type=%s', type(payload).__name__)
            return Response({'status': 'ok', 'received': True, 'saved': False}, status=200)

        normalized = normalize_webhook_payload(payload)

        alert_id = normalized.get('alert_id') or payload.get('Alert ID') or payload.get('alert_id')
        if alert_id is not None and str(alert_id).strip() != '':
            cache_key = f'onestepgps:webhook:{location_id or "global"}:{alert_id}'
            if cache.get(cache_key):
                logger.info('OneStepGPS duplicate webhook ignored alert_id=%s', alert_id)
                return Response({'status': 'ok', 'duplicate': True}, status=200)
            cache.set(cache_key, True, timeout=60 * 60 * 72)

        created = False
        alert = None
        if account is not None:
            alert, created = persist_webhook_alert(account, payload)
            persist_webhook_fleet_events(account, payload)
        else:
            logger.warning(
                'OneStepGPS webhook without location_id — logged only: %s',
                normalized,
            )

        logger.info(
            'OneStepGPS webhook location_id=%s created=%s alert_id=%s name=%s',
            location_id,
            created,
            getattr(alert, 'external_alert_id', None) or normalized.get('alert_id'),
            getattr(alert, 'alert_name', None) or normalized.get('alert_name'),
        )

        return Response(
            {
                'status': 'ok',
                'received': True,
                'saved': bool(alert and created),
                'duplicate': bool(alert and not created),
            },
            status=200,
        )


@method_decorator(csrf_exempt, name='dispatch')
class OneStepGPSDataQueueView(APIView):
    """
    Official OneStep DataQueue consumer.

    Body is an array of { schema, value } for alert, device_point, drive_stop, dtc.
    No Authentication is supported (VERIFY ENDPOINT uses GET).
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, location_id=None):
        # OneStep "VERIFY ENDPOINT" may GET first; they want HTTP 200.
        return Response({
            'status': 'success',
            'message': 'OneStep DataQueue endpoint is ready.',
            'data': [],
            'location_id': location_id,
        }, status=200)

    def post(self, request, location_id=None):
        from accounts.models import GHLAuthCredentials

        from .bundle_store import persist_operational_bundle
        from .osg_dataqueue import extract_official_dataqueue_bundle

        if not location_id:
            return Response({'detail': 'location_id is required.'}, status=400)

        account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if account is None:
            logger.warning('OneStepGPS DataQueue unknown location_id=%s', location_id)
            return Response({'detail': 'Unknown location.'}, status=404)

        integration = OneStepGPSIntegration.get_for_account(account)
        payload = _parse_webhook_payload(request)
        bundle = extract_official_dataqueue_bundle(payload)
        stats = persist_operational_bundle(account, bundle)

        integration.last_webhook_at = timezone.now()
        integration.save(update_fields=['last_webhook_at'])

        preview = _dataqueue_payload_preview(payload)
        logger.info(
            'OneStepGPS DataQueue location_id=%s items=%s preview=%s alerts=%s trips=%s maintenance=%s',
            location_id,
            stats.get('events'),
            preview,
            stats.get('alerts'),
            stats.get('trips'),
            stats.get('maintenance'),
        )
        # Official snippet + VERIFY ENDPOINT both expect HTTP 200, not 202.
        return Response(
            {
                'status': 'success',
                'message': 'DataQueue batch received.',
                'data': preview.get('schemas') or [],
                'processed': stats.get('events', 0),
                'normalized': {
                    'alerts': stats.get('alerts', 0),
                    'trips': stats.get('trips', 0),
                    'maintenance': stats.get('maintenance', 0),
                    'vehicles': stats.get('vehicles', 0),
                },
            },
            status=200,
        )
