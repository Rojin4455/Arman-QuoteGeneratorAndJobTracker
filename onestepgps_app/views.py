import base64
import binascii
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


def _verify_onestepgps_basic_auth(request, integration=None):
    """
    Validate Basic credentials from Authorization or Authentication header.
    Uses per-account webhook credentials when provided, else global settings.
    """
    if integration is not None:
        expected_user = (integration.webhook_username or '').strip()
        expected_pass = integration.webhook_password or ''
    else:
        expected_user = (getattr(settings, 'ONESTEPGPS_WEBHOOK_USERNAME', None) or '').strip()
        expected_pass = getattr(settings, 'ONESTEPGPS_WEBHOOK_PASSWORD', None) or ''

    if not expected_user and not expected_pass:
        logger.warning(
            'OneStepGPS webhook credentials not set; webhook accepts POST without Basic auth'
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
        return Response(serializer.data)


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

        if not _verify_onestepgps_basic_auth(request, integration=integration):
            return Response(
                {'detail': 'Unauthorized'},
                status=401,
                headers={'WWW-Authenticate': 'Basic realm="OneStepGPS"'},
            )

        payload = request.data if isinstance(request.data, dict) else {}
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
