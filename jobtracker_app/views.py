import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Count, Sum, Min, Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from accounts.models import Webhook
from service_app.models import User, Appointment
from .models import Job, JobOccurrence
from .serializers import (
    CalendarEventSerializer,
    JobSeriesCreateSerializer,
    JobSerializer,
    LocationSummarySerializer,
    OccurrenceEventSerializer,
    AppointmentCalendarSerializer,
    AppointmentSerializer,
)
from .tasks import handle_webhook_event


def apply_job_filters(queryset, request):
    """
    Apply common filters to job queryset based on query parameters.
    Supports:
    - status: comma-separated list of statuses (e.g., 'pending,confirmed')
    - job_type: comma-separated list of job types (e.g., 'one_time,recurring')
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
    
    # Filter by job_type (supports multiple job types)
    job_type = params.get('job_type')
    if job_type:
        job_type_list = [jt.strip() for jt in job_type.split(',') if jt.strip()]
        # Validate against valid choices
        valid_types = ['one_time', 'recurring']
        job_type_list = [jt for jt in job_type_list if jt in valid_types]
        if job_type_list:
            queryset = queryset.filter(job_type__in=job_type_list)
    
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
        # Only admins can create jobs
        # Normal users can update/delete their own jobs
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), _IsAdminOnly()]  # type: ignore
        elif self.request.method in ['PUT', 'PATCH', 'DELETE']:
            return [permissions.IsAuthenticated()]  # Allow authenticated users to update/delete
        return super().get_permissions()

    def get_object(self):
        """Override to ensure users can only access jobs assigned to them."""
        obj = super().get_object()
        user = self.request.user
        
        # Admins can access any job
        if getattr(user, 'is_admin', False):
            return obj
        
        # Normal users can only access jobs assigned to them
        if not obj.assignments.filter(user=user).exists():
            raise PermissionDenied("You do not have permission to access this job.")
        
        return obj

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


class AppointmentCalendarView(APIView):
    """Calendar view for appointments in a date range.
    Query params: 
    - start (ISO), end (ISO) - required for date range
    - status: comma-separated list of appointment statuses
    - assigned_user_ids: comma-separated list of user UUIDs or emails
    - search: search in title, notes
    Returns all appointments with start_time in the range.
    - Admins: all appointments
    - Normal user: only appointments assigned to them or where they are in users list
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

        # Query Appointment model
        qs = Appointment.objects.filter(
            start_time__gte=start_dt,
            start_time__lte=end_dt,
        ).exclude(start_time__isnull=True).select_related('assigned_user', 'contact').prefetch_related('users')

        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        
        is_admin = getattr(user, 'is_admin', False)
        
        # Filter by assigned users (check this first for admin users)
        assigned_user_ids = request.query_params.get('assigned_user_ids')
        
        # For admin users: require assigned_user_ids to be provided, otherwise return empty
        if is_admin:
            if not assigned_user_ids:
                # Return empty appointments if assigned_user_ids is not provided
                return Response([], status=200)
            
            # Parse assigned_user_ids for admin
            assigned_list = [a.strip() for a in assigned_user_ids.split(',') if a.strip()]
            if not assigned_list:
                # Return empty if assigned_user_ids is empty after parsing
                return Response([], status=200)
            
            user_ids = []
            for assignee in assigned_list:
                try:
                    user_id = uuid.UUID(assignee)
                    user_ids.append(user_id)
                except (ValueError, AttributeError):
                    user_obj = User.objects.filter(
                        Q(email=assignee) | Q(username=assignee)
                    ).first()
                    if user_obj:
                        user_ids.append(user_obj.id)
            
            if user_ids:
                qs = qs.filter(assigned_user__id__in=user_ids)
            else:
                # No valid user IDs found, return empty
                return Response([], status=200)
        else:
            # Normal users: only appointments assigned to them or where they are in users list
            qs = qs.filter(
                Q(assigned_user=user) | Q(users=user)
            ).distinct()
            
            # Filter by assigned users (optional for normal users)
            if assigned_user_ids:
                assigned_list = [a.strip() for a in assigned_user_ids.split(',') if a.strip()]
                if assigned_list:
                    user_ids = []
                    for assignee in assigned_list:
                        try:
                            user_id = uuid.UUID(assignee)
                            user_ids.append(user_id)
                        except (ValueError, AttributeError):
                            user_obj = User.objects.filter(
                                Q(email=assignee) | Q(username=assignee)
                            ).first()
                            if user_obj:
                                user_ids.append(user_obj.id)
                    if user_ids:
                        qs = qs.filter(assigned_user__id__in=user_ids)

        # Filter by status
        status = request.query_params.get('status')
        if status:
            status_list = [s.strip() for s in status.split(',') if s.strip()]
            if status_list:
                qs = qs.filter(appointment_status__in=status_list)

        # Search filter
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(notes__icontains=search)
            )

        data = AppointmentCalendarSerializer(qs.order_by('start_time'), many=True).data
        return Response(data)


class AppointmentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CRUD operations on appointments.
    
    List/Create: GET/POST /api/jobtracker/appointments/
    Retrieve/Update/Delete: GET/PUT/PATCH/DELETE /api/jobtracker/appointments/{id}/
    
    Permissions:
    - Admins: Full access to all appointments
    - Normal users: Can only access appointments assigned to them or where they are in users list
    
    Query Parameters (filters):
    - status: comma-separated list of appointment statuses (e.g., 'new,confirmed,cancelled')
    - assigned_user_ids: comma-separated list of user UUIDs or emails
    - assigned_user_id: single user UUID or email
    - users: comma-separated list of user UUIDs (filter by users in many-to-many)
    - contact_id: filter by contact ID
    - location_id: filter by location ID
    - calendar_id: filter by calendar ID
    - source: filter by source
    - start_date: filter by start_time >= date (YYYY-MM-DD format)
    - end_date: filter by start_time <= date (YYYY-MM-DD format)
    - search: search in title and notes (case-insensitive)
    """
    serializer_class = AppointmentSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    lookup_field = 'id'

    def get_queryset(self):
        """Filter queryset based on user permissions and query parameters"""
        user = self.request.user
        
        if not user.is_authenticated:
            return Appointment.objects.none()
        
        qs = Appointment.objects.select_related(
            'assigned_user', 'contact'
        ).prefetch_related('users').all()
        
        is_admin = getattr(user, 'is_admin', False)
        
        # Permission filtering
        if not is_admin:
            # Normal users: only appointments assigned to them or where they are in users list
            qs = qs.filter(
                Q(assigned_user=user) | Q(users=user)
            ).distinct()
        
        # Filter by status (comma-separated list)
        status = self.request.query_params.get('status')
        if status:
            status_list = [s.strip() for s in status.split(',') if s.strip()]
            if status_list:
                qs = qs.filter(appointment_status__in=status_list)
        
        # Filter by assigned_user_ids (comma-separated list of UUIDs or emails)
        assigned_user_ids = self.request.query_params.get('assigned_user_ids')
        if assigned_user_ids:
            assigned_list = [a.strip() for a in assigned_user_ids.split(',') if a.strip()]
            if assigned_list:
                user_ids = []
                for assignee in assigned_list:
                    try:
                        user_id = uuid.UUID(assignee)
                        user_ids.append(user_id)
                    except (ValueError, AttributeError):
                        user_obj = User.objects.filter(
                            Q(email=assignee) | Q(username=assignee)
                        ).first()
                        if user_obj:
                            user_ids.append(user_obj.id)
                if user_ids:
                    qs = qs.filter(assigned_user__id__in=user_ids)
        
        # Filter by assigned_user_id (single UUID or email)
        assigned_user_id = self.request.query_params.get('assigned_user_id')
        if assigned_user_id:
            try:
                user_id = uuid.UUID(assigned_user_id.strip())
                qs = qs.filter(assigned_user__id=user_id)
            except (ValueError, AttributeError):
                user_obj = User.objects.filter(
                    Q(email=assigned_user_id.strip()) | Q(username=assigned_user_id.strip())
                ).first()
                if user_obj:
                    qs = qs.filter(assigned_user=user_obj)
        
        # Filter by users (comma-separated list of UUIDs in many-to-many)
        users_param = self.request.query_params.get('users')
        if users_param:
            users_list = [u.strip() for u in users_param.split(',') if u.strip()]
            if users_list:
                user_ids = []
                for user_identifier in users_list:
                    try:
                        user_id = uuid.UUID(user_identifier)
                        user_ids.append(user_id)
                    except (ValueError, AttributeError):
                        user_obj = User.objects.filter(
                            Q(email=user_identifier) | Q(username=user_identifier)
                        ).first()
                        if user_obj:
                            user_ids.append(user_obj.id)
                if user_ids:
                    qs = qs.filter(users__id__in=user_ids).distinct()
        
        # Filter by contact_id
        contact_id = self.request.query_params.get('contact_id')
        if contact_id:
            qs = qs.filter(contact__contact_id=contact_id)
        
        # Filter by location_id
        location_id = self.request.query_params.get('location_id')
        if location_id:
            qs = qs.filter(location_id=location_id)
        
        # Filter by calendar_id
        calendar_id = self.request.query_params.get('calendar_id')
        if calendar_id:
            qs = qs.filter(calendar_id=calendar_id)
        
        # Filter by source
        source = self.request.query_params.get('source')
        if source:
            qs = qs.filter(source=source)
        
        # Filter by date range (start_date and end_date)
        start_date = self.request.query_params.get('start_date')
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                qs = qs.filter(start_time__gte=start_dt)
            except ValueError:
                pass
        
        end_date = self.request.query_params.get('end_date')
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                qs = qs.filter(start_time__lt=end_dt)
            except ValueError:
                pass
        
        # Search filter (title and notes)
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(notes__icontains=search)
            )
        
        return qs.order_by('-start_time', '-created_at')

    def get_object(self):
        """Override to check permissions on individual object"""
        obj = super().get_object()
        user = self.request.user
        
        # Admins can access any appointment
        if getattr(user, 'is_admin', False):
            return obj
        
        # Normal users: only if assigned to them or in users list
        if obj.assigned_user != user and user not in obj.users.all():
            raise PermissionDenied("You don't have permission to access this appointment.")
        
        return obj

    def perform_create(self, serializer):
        """Set location_id from credentials if not provided"""
        if 'location_id' not in serializer.validated_data or not serializer.validated_data.get('location_id'):
            from accounts.models import GHLAuthCredentials
            credentials = GHLAuthCredentials.objects.first()
            if credentials and credentials.location_id:
                serializer.save(location_id=credentials.location_id)
            else:
                serializer.save()
        else:
            serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Delete appointment"""
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': 'Appointment deleted successfully'}, status=204)


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




@csrf_exempt
def webhook_handler(request):
    if request.method != "POST":
        return JsonResponse({"message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        print("date:----- ", data)
        Webhook.objects.create(
            event=data.get("event") or "jobtracker.invoice",
            company_id=str(data.get("company_id") or data.get("location_id") or "unknown"),
            payload=data,
        )
        handle_webhook_event.delay(data)
        return JsonResponse({"message": "Webhook received"}, status=200)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)