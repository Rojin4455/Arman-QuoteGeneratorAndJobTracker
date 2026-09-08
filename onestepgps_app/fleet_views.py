from datetime import timedelta

from django.db.models import Sum, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import AccountScopedPermission, IsAdminPermission, IsManagementUserPermission
from service_app.models import User

from .models import (
    OneStepGPSAlert,
    OneStepGPSGeofence,
    OneStepGPSMaintenance,
    OneStepGPSTrip,
    OneStepGPSVehicleBinding,
)
from .serializers import (
    OneStepGPSGeofenceSerializer,
    OneStepGPSMaintenanceSerializer,
    OneStepGPSTripSerializer,
    OneStepGPSVehicleBindingSerializer,
)


class FleetTripsView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        qs = OneStepGPSTrip.objects.filter(account=request.account)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(device_name__icontains=search) | Q(device_id__icontains=search) | Q(start_address__icontains=search))
        trips = qs[:200]
        distance = qs.aggregate(total=Sum('distance_miles')).get('total') or 0
        drive_seconds = qs.filter(kind='drive').aggregate(total=Sum('duration_seconds')).get('total') or 0
        stops = qs.filter(kind='stop').count()
        return Response({
            'results': OneStepGPSTripSerializer(trips, many=True).data,
            'count': qs.count(),
            'totals': {
                'distance_miles': round(float(distance), 1),
                'drive_seconds': int(drive_seconds),
                'stops': stops,
            },
        })


class FleetMaintenanceView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        rows = list(OneStepGPSMaintenance.objects.filter(account=request.account).order_by('device_name'))
        fuels = [r.fuel_level_percent for r in rows if r.fuel_level_percent is not None]
        need_attention = sum(1 for r in rows if r.check_engine or (r.dtc_codes or []))
        avg_fuel = round(sum(fuels) / len(fuels), 0) if fuels else None
        return Response({
            'results': OneStepGPSMaintenanceSerializer(rows, many=True).data,
            'totals': {
                'need_attention': need_attention,
                'average_fuel': avg_fuel,
                'vehicles_monitored': len(rows),
            },
        })


class FleetGeofenceListCreateView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        qs = OneStepGPSGeofence.objects.filter(account=request.account)
        return Response({
            'results': OneStepGPSGeofenceSerializer(qs, many=True).data,
            'count': qs.count(),
        })

    def post(self, request):
        serializer = OneStepGPSGeofenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(account=request.account)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class FleetGeofenceDetailView(APIView):
    permission_classes = [AccountScopedPermission, IsAdminPermission]

    def _get(self, request, pk):
        return OneStepGPSGeofence.objects.filter(account=request.account, pk=pk).first()

    def patch(self, request, pk):
        obj = self._get(request, pk)
        if not obj:
            return Response({'detail': 'Not found.'}, status=404)
        serializer = OneStepGPSGeofenceSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        obj = self._get(request, pk)
        if not obj:
            return Response({'detail': 'Not found.'}, status=404)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FleetAssignmentsView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        rows = OneStepGPSVehicleBinding.objects.filter(account=request.account).select_related('user')
        return Response({'results': OneStepGPSVehicleBindingSerializer(rows, many=True).data})

    def put(self, request):
        items = request.data.get('assignments') or request.data.get('results') or []
        if not isinstance(items, list):
            return Response({'detail': 'assignments must be a list.'}, status=400)

        saved = []
        for item in items:
            device_id = str(item.get('device_id') or '').strip()
            if not device_id:
                continue
            user_id = item.get('user') or item.get('user_id')
            user = None
            technician_name = (item.get('technician_name') or '').strip()
            if user_id:
                user = User.objects.filter(account=request.account, pk=user_id).first()
                if user and not technician_name:
                    technician_name = user.get_full_name() or user.username
            obj, _ = OneStepGPSVehicleBinding.objects.update_or_create(
                account=request.account,
                device_id=device_id,
                defaults={'user': user, 'technician_name': technician_name},
            )
            saved.append(obj)
        return Response({'results': OneStepGPSVehicleBindingSerializer(saved, many=True).data})


class FleetAlertAcknowledgeView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def post(self, request, pk):
        alert = OneStepGPSAlert.objects.filter(account=request.account, pk=pk).first()
        if not alert:
            return Response({'detail': 'Not found.'}, status=404)
        alert.acknowledged = True
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['acknowledged', 'acknowledged_at'])
        return Response({'id': str(alert.id), 'acknowledged': True, 'acknowledged_at': alert.acknowledged_at})


class FleetReportsView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        since = timezone.now() - timedelta(days=30)
        trips = OneStepGPSTrip.objects.filter(account=request.account, started_at__gte=since)
        alerts = OneStepGPSAlert.objects.filter(account=request.account).filter(
            Q(alert_time__gte=since) | Q(alert_time__isnull=True, created_at__gte=since)
        )
        maint = OneStepGPSMaintenance.objects.filter(account=request.account)
        geofence_alerts = alerts.filter(alert_name__icontains='geofence')
        safety = alerts.filter(
            Q(alert_name__icontains='speed')
            | Q(alert_name__icontains='harsh')
            | Q(alert_name__icontains='brak')
        )
        miles = trips.aggregate(total=Sum('distance_miles')).get('total') or 0
        return Response({
            'period_days': 30,
            'fleet_activity': {
                'distance_miles': round(float(miles), 1),
                'trips': trips.filter(kind='drive').count(),
                'stops': trips.filter(kind='stop').count(),
                'idle_seconds': int(trips.aggregate(total=Sum('idle_seconds')).get('total') or 0),
            },
            'driver_safety': {
                'events': safety.count(),
                'speeding': safety.filter(alert_name__icontains='speed').count(),
                'harsh': safety.filter(Q(alert_name__icontains='harsh') | Q(alert_name__icontains='brak')).count(),
            },
            'maintenance_health': {
                'vehicles': maint.count(),
                'need_attention': sum(1 for r in maint if r.check_engine or (r.dtc_codes or [])),
                'dtc_vehicles': maint.exclude(dtc_codes=[]).count(),
            },
            'location_activity': {
                'geofence_events': geofence_alerts.count(),
                'active_geofences': OneStepGPSGeofence.objects.filter(account=request.account, is_active=True).count(),
            },
        })


class FleetSummaryView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        alerts = OneStepGPSAlert.objects.filter(account=request.account)
        open_alerts = alerts.filter(acknowledged=False).count()
        return Response({
            'open_alerts': open_alerts,
            'critical_alerts': sum(1 for a in alerts.filter(acknowledged=False)[:200] if a.severity == 'critical'),
            'acknowledged_alerts': alerts.filter(acknowledged=True).count(),
            'trips': OneStepGPSTrip.objects.filter(account=request.account).count(),
            'geofences': OneStepGPSGeofence.objects.filter(account=request.account, is_active=True).count(),
            'maintenance_attention': sum(
                1
                for r in OneStepGPSMaintenance.objects.filter(account=request.account)
                if r.check_engine or (r.dtc_codes or [])
            ),
        })
