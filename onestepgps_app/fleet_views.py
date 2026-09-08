from datetime import timedelta

from django.db.models import Sum, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import AccountScopedPermission, IsManagementUserPermission
from service_app.models import User

from .models import (
    OneStepGPSAlert,
    OneStepGPSGeofence,
    OneStepGPSMaintenance,
    OneStepGPSServiceLog,
    OneStepGPSTrip,
    OneStepGPSVehicleBinding,
)
from .fleet_reports import build_fleet_reports
from .maintenance_utils import due_info, parse_service_datetime, roll_next_service
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
        start = parse_datetime(request.query_params.get('start') or '')
        end = parse_datetime(request.query_params.get('end') or '')
        if start:
            qs = qs.filter(started_at__gte=start)
        if end:
            qs = qs.filter(started_at__lte=end)
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
        rows = list(
            OneStepGPSMaintenance.objects.filter(account=request.account)
            .prefetch_related('logs')
            .order_by('device_name')
        )
        for row in rows:
            row._prefetched_logs = list(row.logs.all()[:8])
        fuels = [r.fuel_level_percent for r in rows if r.fuel_level_percent is not None]
        due = [due_info(r) for r in rows]
        need_attention = sum(1 for info in due if info['needs_attention'])
        avg_fuel = round(sum(fuels) / len(fuels), 0) if fuels else None
        return Response({
            'results': OneStepGPSMaintenanceSerializer(rows, many=True).data,
            'totals': {
                'need_attention': need_attention,
                'due_soon': sum(1 for info in due if info['due_status'] == 'due_soon'),
                'overdue': sum(1 for info in due if info['due_status'] == 'overdue'),
                'average_fuel': avg_fuel,
                'vehicles_monitored': len(rows),
            },
        })


class FleetMaintenanceDetailView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def _get(self, request, pk):
        return OneStepGPSMaintenance.objects.filter(account=request.account, pk=pk).first()

    def patch(self, request, pk):
        obj = self._get(request, pk)
        if not obj:
            return Response({'detail': 'Not found.'}, status=404)
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        if 'next_service_at' in data:
            data['next_service_at'] = parse_service_datetime(data.get('next_service_at'))
        for key in ('next_service_miles', 'interval_miles', 'interval_days'):
            if key in data and data[key] in ('', None):
                data[key] = None
        serializer = OneStepGPSMaintenanceSerializer(obj, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save(schedule_managed=True)
        return Response(OneStepGPSMaintenanceSerializer(obj).data)


class FleetMaintenanceCompleteView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def post(self, request, pk):
        obj = OneStepGPSMaintenance.objects.filter(account=request.account, pk=pk).first()
        if not obj:
            return Response({'detail': 'Not found.'}, status=404)

        performed_at = parse_service_datetime(request.data.get('performed_at')) or timezone.now()
        odometer = request.data.get('odometer_miles')
        try:
            odometer = float(odometer) if odometer not in (None, '') else obj.odometer_miles
        except (TypeError, ValueError):
            odometer = obj.odometer_miles
        service_type = (request.data.get('service_type') or obj.service_type or 'service').strip()[:64]
        notes = str(request.data.get('notes') or '')[:2000]

        log = OneStepGPSServiceLog.objects.create(
            account=request.account,
            maintenance=obj,
            device_id=obj.device_id,
            device_name=obj.device_name,
            service_type=service_type,
            performed_at=performed_at,
            odometer_miles=odometer,
            notes=notes,
        )
        obj.last_service_at = performed_at
        obj.last_service_miles = odometer
        if service_type:
            obj.service_type = service_type
        obj.schedule_managed = True
        roll_next_service(obj, performed_at, odometer)
        next_miles = request.data.get('next_service_miles')
        if next_miles not in (None, ''):
            try:
                obj.next_service_miles = float(next_miles)
            except (TypeError, ValueError):
                pass
        if 'next_service_at' in request.data:
            parsed = parse_service_datetime(request.data.get('next_service_at'))
            if parsed:
                obj.next_service_at = parsed
        obj.save()
        return Response({
            'maintenance': OneStepGPSMaintenanceSerializer(obj).data,
            'log': {
                'id': str(log.id),
                'service_type': log.service_type,
                'performed_at': log.performed_at,
            },
        }, status=status.HTTP_201_CREATED)


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
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

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
        return Response(build_fleet_reports(request.account, days=30))


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
                if due_info(r)['needs_attention']
            ),
        })
