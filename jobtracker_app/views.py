from rest_framework import viewsets, permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.db.models import Count, Sum, Min, Q
from django.db.models.functions import Coalesce
from collections import defaultdict

from .models import Job, JTService, JobOccurrence
from .serializers import JobSerializer, JTServiceSerializer, OccurrenceEventSerializer, JobSeriesCreateSerializer, CalendarEventSerializer,LocationSummarySerializer
from service_app.models import User


class IsAuthenticatedOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_authenticated


class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.all().select_related('submission')
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()

        submission_id = self.request.query_params.get('submission_id')
        if submission_id:
            queryset = queryset.filter(submission_id=submission_id)

        user = self.request.user
        if not user.is_authenticated:
            return queryset.none()

        if getattr(user, 'is_admin', False):
            return queryset

        # Normal users: only jobs assigned to them
        return queryset.filter(assignments__user=user).distinct()

    def get_permissions(self):
        # Only admins can create/update/delete jobs
        if self.request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
            return [permissions.IsAuthenticated(), _IsAdminOnly()]  # type: ignore
        return super().get_permissions()

    @action(detail=False, methods=['get'], url_path='mine')
    def mine(self, request):
        """Convenience endpoint: jobs for the authenticated user (by email)."""
        if not request.user.is_authenticated:
            return Response([], status=200)
        qs = self.get_queryset()
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, created_by_email=getattr(self.request.user, 'email', None))


class JTServiceViewSet(viewsets.ModelViewSet):
    queryset = JTService.objects.all().order_by('name')
    serializer_class = JTServiceSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return qs.none()
        if getattr(user, 'is_admin', False):
            return qs
        # Normal users only see their own templates
        return qs.filter(created_by=user)

    def perform_create(self, serializer):
        creator = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=creator)


class _IsAdminOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and getattr(request.user, 'is_admin', False)


from rest_framework.views import APIView
from django.utils.dateparse import parse_datetime

class OccurrenceListView(APIView):
    """Flattened calendar events for a date range.
    Query params: start (ISO), end (ISO)
    Returns all jobs (one-time and recurring series instances) with scheduled_at in the range.
    - Admins: all jobs
    - Normal user: only jobs assigned to them
    """
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if not start or not end:
            return Response({'detail': 'start and end are required (ISO strings).'}, status=400)

        start_dt = parse_datetime(start)
        end_dt = parse_datetime(end)
        if not start_dt or not end_dt:
            return Response({'detail': 'Invalid start/end datetime.'}, status=400)

        # Query Job model directly (includes both one-time jobs and recurring series instances)
        qs = Job.objects.filter(
            scheduled_at__gte=start_dt,
            scheduled_at__lte=end_dt,
        ).exclude(scheduled_at__isnull=True)

        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        if not getattr(user, 'is_admin', False):
            qs = qs.filter(assignments__user=user).distinct()

        data = CalendarEventSerializer(qs.order_by('scheduled_at', 'series_sequence'), many=True).data
        return Response(data)


class JobSeriesCreateView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def post(self, request):
        # Only admins can create series
        if not (request.user.is_authenticated and getattr(request.user, 'is_admin', False)):
            raise permissions.PermissionDenied('Admin only')
        serializer = JobSeriesCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response(result, status=201)


class JobBySeriesView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, series_id):
        qs = Job.objects.filter(series_id=series_id).select_related('submission').order_by('series_sequence')
        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        if not getattr(user, 'is_admin', False):
            qs = qs.filter(assignments__user=user).distinct()
        data = JobSerializer(qs, many=True).data
        return Response(data)



class LocationJobListView(APIView):
    """
    Returns jobs grouped by location with summary statistics.
    Each location includes:
    - Address details
    - Number of jobs
    - Customer names
    - Status counts
    - Total price
    - Total hours
    - Next scheduled date
    - Service names
    """
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)

        # Base queryset with permission filtering
        qs = Job.objects.select_related('submission').prefetch_related(
            'items__service', 'assignments__user'
        )

        if not getattr(user, 'is_admin', False):
            qs = qs.filter(assignments__user=user).distinct()

        # Filter out jobs without addresses
        qs = qs.exclude(Q(customer_address__isnull=True) | Q(customer_address=''))

        # Group jobs by address
        location_map = defaultdict(list)
        for job in qs:
            address = job.customer_address.strip()
            location_map[address].append(job)

        # Build response data
        result = []
        for address, jobs in location_map.items():
            # Status counts
            status_counts = defaultdict(int)
            for job in jobs:
                status_counts[job.status] += 1

            # Customer names (unique)
            customer_names = list(set(
                job.customer_name for job in jobs 
                if job.customer_name
            ))

            # Total price and hours
            total_price = sum(job.total_price for job in jobs)
            total_hours = sum(job.duration_hours for job in jobs)

            # Next scheduled job
            next_scheduled = None
            scheduled_jobs = [j for j in jobs if j.scheduled_at]
            if scheduled_jobs:
                next_job = min(scheduled_jobs, key=lambda j: j.scheduled_at)
                next_scheduled = next_job.scheduled_at

            # Service names (unique)
            service_names = set()
            for job in jobs:
                for item in job.items.all():
                    if item.service and item.service.name:
                        service_names.add(item.service.name)
                    elif item.custom_name:
                        service_names.add(item.custom_name)

            result.append({
                'address': address,
                'job_count': len(jobs),
                'customer_names': customer_names,
                'status_counts': {
                    'pending': status_counts.get('pending', 0),
                    'confirmed': status_counts.get('confirmed', 0),
                    'service_due': status_counts.get('service_due', 0),
                    'on_the_way': status_counts.get('on_the_way', 0),
                    'in_progress': status_counts.get('in_progress', 0),
                    'completed': status_counts.get('completed', 0),
                    'cancelled': status_counts.get('cancelled', 0),
                },
                'total_price': float(total_price),
                'total_hours': float(total_hours),
                'next_scheduled': next_scheduled.isoformat() if next_scheduled else None,
                'service_names': sorted(list(service_names)),
                'job_ids': [str(job.id) for job in jobs],
            })

        # Sort by next scheduled date (nulls last)
        result.sort(key=lambda x: (x['next_scheduled'] is None, x['next_scheduled']))

        return Response(result)


class LocationJobDetailView(APIView):
    """
    Returns detailed job information for a specific location.
    Query param: address (exact match)
    """
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        address = request.query_params.get('address')
        if not address:
            return Response({'detail': 'address query parameter is required.'}, status=400)

        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)

        # Base queryset with permission filtering
        qs = Job.objects.filter(customer_address=address).select_related('submission')

        if not getattr(user, 'is_admin', False):
            qs = qs.filter(assignments__user=user).distinct()

        # Serialize and return
        data = JobSerializer(qs.order_by('scheduled_at', '-created_at'), many=True).data
        return Response(data)