from rest_framework import viewsets, permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.db.models import Count, Sum, Min, Q
from django.db.models.functions import Coalesce
from collections import defaultdict
from django.utils.dateparse import parse_datetime
import uuid

from .models import Job, JobOccurrence
from .serializers import JobSerializer, OccurrenceEventSerializer, JobSeriesCreateSerializer, CalendarEventSerializer,LocationSummarySerializer
from service_app.models import User


def apply_job_filters(queryset, request):
    """
    Apply common filters to job queryset based on query parameters.
    Supports:
    - status: comma-separated list of statuses (e.g., 'pending,confirmed')
    - job_ids: comma-separated list of job UUIDs
    - assignee_ids: comma-separated list of user UUIDs or emails
    - start_date: ISO datetime string (filters scheduled_at >= start_date)
    - end_date: ISO datetime string (filters scheduled_at <= end_date)
    - search: search in title, description, customer_name, customer_email, customer_phone
    """
    params = request.query_params
    
    # Filter by status (supports multiple statuses)
    status = params.get('status')
    if status:
        status_list = [s.strip() for s in status.split(',') if s.strip()]
        if status_list:
            queryset = queryset.filter(status__in=status_list)
    else:
        queryset = queryset.exclude(
            Q(status__isnull=True) | Q(status="") | Q(status="to_convert")
        )
    
    # Filter by specific job IDs
    job_ids = params.get('job_ids')
    if job_ids:
        try:
            id_list = [uuid.UUID(jid.strip()) for jid in job_ids.split(',') if jid.strip()]
            if id_list:
                queryset = queryset.filter(id__in=id_list)
        except (ValueError, AttributeError):
            pass  # Invalid UUID format, skip this filter
    
    # Filter by assignees (user IDs or emails)
    assignee_ids = params.get('assignee_ids')
    if assignee_ids:
        assignee_list = [a.strip() for a in assignee_ids.split(',') if a.strip()]
        if assignee_list:
            # Try to resolve each assignee (could be UUID, email, or username)
            user_ids = []
            for assignee in assignee_list:
                try:
                    # Try as UUID first
                    user_id = uuid.UUID(assignee)
                    user_ids.append(user_id)
                except (ValueError, AttributeError):
                    # Try as email or username
                    user = User.objects.filter(
                        Q(email=assignee) | Q(username=assignee)
                    ).first()
                    if user:
                        user_ids.append(user.id)
            
            if user_ids:
                queryset = queryset.filter(assignments__user_id__in=user_ids).distinct()
    
    # Filter by date range
    start_date = params.get('start_date')
    if start_date:
        start_dt = parse_datetime(start_date)
        if start_dt:
            queryset = queryset.filter(scheduled_at__gte=start_dt)
    
    end_date = params.get('end_date')
    if end_date:
        end_dt = parse_datetime(end_date)
        if end_dt:
            queryset = queryset.filter(scheduled_at__lte=end_dt)
    
    # Search filter (searches in multiple fields)
    search = params.get('search')
    if search:
        search_query = Q(
            Q(title__icontains=search) |
            Q(description__icontains=search) |
            Q(customer_name__icontains=search) |
            Q(customer_email__icontains=search) |
            Q(customer_phone__icontains=search) |
            Q(customer_address__icontains=search) |
            Q(notes__icontains=search)
        )
        queryset = queryset.filter(search_query)
    
    return queryset


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
            # Apply filters for admins
            queryset = apply_job_filters(queryset, self.request)
            return queryset

        # Normal users: only jobs assigned to them
        queryset = queryset.filter(assignments__user=user).distinct()
        # Apply filters for normal users
        queryset = apply_job_filters(queryset, self.request)
        return queryset

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


class _IsAdminOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and getattr(request.user, 'is_admin', False)


from rest_framework.views import APIView

class OccurrenceListView(APIView):
    """Flattened calendar events for a date range.
    Query params: 
    - start (ISO), end (ISO) - required for date range
    - status: comma-separated list of statuses
    - job_ids: comma-separated list of job UUIDs
    - assignee_ids: comma-separated list of user UUIDs or emails
    - search: search in title, description, customer fields
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

        # Apply additional filters
        qs = apply_job_filters(qs, request)

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
    """
    Returns jobs for a specific series.
    Query params:
    - page: page number (default: 1)
    - page_size: number of items per page (default: 20, max: 100)
    """
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, series_id):
        qs = Job.objects.filter(series_id=series_id).select_related('submission').order_by('series_sequence')
        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        if not getattr(user, 'is_admin', False):
            qs = qs.filter(assignments__user=user).distinct()
        
        # Apply pagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        paginator.page_size_query_param = 'page_size'
        paginator.max_page_size = 100
        
        paginated_qs = paginator.paginate_queryset(qs, request)
        serializer = JobSerializer(paginated_qs, many=True)
        return paginator.get_paginated_response(serializer.data)



class LocationJobListView(APIView):
    """
    Returns jobs grouped by location with summary statistics.
    Query params:
    - status: comma-separated list of statuses
    - job_ids: comma-separated list of job UUIDs
    - assignee_ids: comma-separated list of user UUIDs or emails
    - start_date: ISO datetime string (filters scheduled_at >= start_date)
    - end_date: ISO datetime string (filters scheduled_at <= end_date)
    - search: search in title, description, customer fields
    - page: page number (default: 1)
    - page_size: number of items per page (default: 20, max: 100)
    
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

        # Apply filters
        qs = apply_job_filters(qs, request)

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

        # Apply pagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        paginator.page_size_query_param = 'page_size'
        paginator.max_page_size = 100
        
        paginated_result = paginator.paginate_queryset(result, request)
        return paginator.get_paginated_response(paginated_result)


class LocationJobDetailView(APIView):
    """
    Returns detailed job information for a specific location.
    Query params:
    - address (required): exact match for customer address
    - status: comma-separated list of statuses
    - job_ids: comma-separated list of job UUIDs
    - assignee_ids: comma-separated list of user UUIDs or emails
    - start_date: ISO datetime string (filters scheduled_at >= start_date)
    - end_date: ISO datetime string (filters scheduled_at <= end_date)
    - search: search in title, description, customer fields
    - page: page number (default: 1)
    - page_size: number of items per page (default: 20, max: 100)
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

        # Apply filters
        qs = apply_job_filters(qs, request)

        # Order queryset
        qs = qs.order_by('scheduled_at', '-created_at')

        # Apply pagination
        paginator = PageNumberPagination()
        paginator.page_size = 20
        paginator.page_size_query_param = 'page_size'
        paginator.max_page_size = 100
        
        paginated_qs = paginator.paginate_queryset(qs, request)
        serializer = JobSerializer(paginated_qs, many=True)
        return paginator.get_paginated_response(serializer.data)