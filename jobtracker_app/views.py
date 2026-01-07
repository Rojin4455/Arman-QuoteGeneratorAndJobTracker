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
from .models import Job, JobOccurrence, JobServiceItem, JobAssignment
from .ghl_appointment_sync import delete_appointment_from_ghl
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


def resolve_user_identifier(identifier):
    """
    Resolve a user identifier to a user ID.
    Tries in order: UUID, integer ID, email, username.
    Returns the user ID if found, None otherwise.
    """
    if not identifier:
        return None
    
    identifier = identifier.strip()
    
    # Try as UUID first (for backward compatibility)
    try:
        user_id = uuid.UUID(identifier)
        # Check if user exists with this UUID (in case UUIDs are used in future)
        user = User.objects.filter(id=user_id).first()
        if user:
            return user.id
    except (ValueError, AttributeError, TypeError):
        pass
    
    # Try as integer ID (current actual ID type)
    try:
        user_id = int(identifier)
        user = User.objects.filter(id=user_id).first()
        if user:
            return user.id
    except (ValueError, TypeError):
        pass
    
    # Try as email or username
    user = User.objects.filter(
        Q(email=identifier) | Q(username=identifier)
    ).first()
    if user:
        return user.id
    
    return None


def apply_job_filters(queryset, request, skip_assignee_ids=False):
    """
    Apply common filters to job queryset based on query parameters.
    Supports:
    - status: comma-separated list of statuses (e.g., 'pending,confirmed')
    - job_type: comma-separated list of job types (e.g., 'one_time,recurring')
    - job_ids: comma-separated list of job UUIDs
    - assignee_ids: comma-separated list of user IDs (integer), UUIDs, or emails
    - start_date: ISO datetime string (filters scheduled_at >= start_date)
    - end_date: ISO datetime string (filters scheduled_at <= end_date)
    - search: search in title, description, customer_name, customer_email, customer_phone
    
    Args:
        queryset: The job queryset to filter
        request: The request object with query parameters
        skip_assignee_ids: If True, skip filtering by assignee_ids (useful when handled separately)
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
    
    # Filter by assignees (user IDs, UUIDs, or emails)
    if not skip_assignee_ids:
        assignee_ids = params.get('assignee_ids')
        if assignee_ids:
            assignee_list = [a.strip() for a in assignee_ids.split(',') if a.strip()]
            if assignee_list:
                # Resolve each assignee identifier to user ID
                user_ids = []
                for assignee in assignee_list:
                    user_id = resolve_user_identifier(assignee)
                    if user_id:
                        user_ids.append(user_id)
                
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

    def retrieve(self, request, *args, **kwargs):
        """
        Override retrieve to optimize queryset for appointment checking
        """
        instance = self.get_object()
        
        # Optimize queryset with prefetch for assignments and related data
        instance = Job.objects.prefetch_related(
            'assignments__user',
            'appointment'
        ).get(pk=instance.pk)
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

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
    - assignee_ids: comma-separated list of user IDs (integer), UUIDs, or emails
    - search: search in title, description, customer fields
    Returns all jobs (one-time and recurring series instances) with scheduled_at in the range.
    - Admins: if assignee_ids provided, only jobs for those assignees; otherwise return empty
    - Normal user: always return only jobs assigned to them (assignee_ids parameter is ignored)
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
        
        is_admin = getattr(user, 'is_admin', False)
        assignee_ids_param = request.query_params.get('assignee_ids')
        
        # Handle assignee_ids filtering based on user role
        skip_assignee_ids_in_filter = False
        if is_admin:
            # Admin: if assignee_ids provided, filter by those assignees only
            # Otherwise, return empty (no jobs)
            if assignee_ids_param:
                assignee_list = [a.strip() for a in assignee_ids_param.split(',') if a.strip()]
                if assignee_list:
                    user_ids = []
                    for assignee in assignee_list:
                        user_id = resolve_user_identifier(assignee)
                        if user_id:
                            user_ids.append(user_id)
                    if user_ids:
                        qs = qs.filter(assignments__user_id__in=user_ids).distinct()
                    else:
                        # No valid user IDs found, return empty
                        qs = qs.none()
                else:
                    # Empty assignee_ids param, return empty
                    qs = qs.none()
                # Skip assignee_ids in apply_job_filters since we handled it above
                skip_assignee_ids_in_filter = True
            else:
                # No assignee_ids provided for admin, return empty
                qs = qs.none()
                skip_assignee_ids_in_filter = True
        else:
            # Non-admin: always filter by their own user (ignore assignee_ids parameter)
            qs = qs.filter(assignments__user=user).distinct()
            # Skip assignee_ids in apply_job_filters since we already filtered by user
            skip_assignee_ids_in_filter = True

        # Apply additional filters
        qs = apply_job_filters(qs, request, skip_assignee_ids=skip_assignee_ids_in_filter)

        data = CalendarEventSerializer(qs.order_by('scheduled_at', 'series_sequence'), many=True).data
        return Response(data)


class AppointmentCalendarView(APIView):
    """Calendar view for appointments in a date range.
    Query params: 
    - start (ISO), end (ISO) - required for date range
    - status: comma-separated list of appointment statuses
    - assigned_user_ids: comma-separated list of user IDs (integer), UUIDs, or emails
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
        print("qs: ", qs)
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
                user_id = resolve_user_identifier(assignee)
                if user_id:
                    user_ids.append(user_id)
            
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
                        user_id = resolve_user_identifier(assignee)
                        if user_id:
                            user_ids.append(user_id)
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
    - assigned_user_ids: comma-separated list of user IDs (integer), UUIDs, or emails
    - assigned_user_id: single user ID (integer), UUID, or email
    - users: comma-separated list of user IDs (integer), UUIDs, or emails (filter by users in many-to-many)
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
            'assigned_user', 'contact', 'calendar'
        ).prefetch_related('users').all()
        
        # Exclude appointments with calendar name "Reccuring Service Calendar"
        qs = qs.exclude(calendar__name="Reccuring Service Calendar")
        
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
        
        # Filter by assigned_user_ids (comma-separated list of IDs, UUIDs, or emails)
        assigned_user_ids = self.request.query_params.get('assigned_user_ids')
        if assigned_user_ids:
            assigned_list = [a.strip() for a in assigned_user_ids.split(',') if a.strip()]
            if assigned_list:
                user_ids = []
                for assignee in assigned_list:
                    user_id = resolve_user_identifier(assignee)
                    if user_id:
                        user_ids.append(user_id)
                if user_ids:
                    qs = qs.filter(assigned_user__id__in=user_ids)
        
        # Filter by assigned_user_id (single ID, UUID, or email)
        assigned_user_id = self.request.query_params.get('assigned_user_id')
        if assigned_user_id:
            user_id = resolve_user_identifier(assigned_user_id)
            if user_id:
                qs = qs.filter(assigned_user__id=user_id)
        
        # Filter by users (comma-separated list of IDs, UUIDs, or emails in many-to-many)
        users_param = self.request.query_params.get('users')
        if users_param:
            users_list = [u.strip() for u in users_param.split(',') if u.strip()]
            if users_list:
                user_ids = []
                for user_identifier in users_list:
                    user_id = resolve_user_identifier(user_identifier)
                    if user_id:
                        user_ids.append(user_id)
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
        
        # Filter by calendar_id (using ForeignKey relationship)
        calendar_id = self.request.query_params.get('calendar_id')
        if calendar_id:
            qs = qs.filter(calendar__ghl_calendar_id=calendar_id)
        
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

    def update(self, request, *args, **kwargs):
        """Update appointment and sync to GHL"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        
        # Store previous field values to detect changes
        previous_fields = {
            'title': instance.title,
            'appointment_status': instance.appointment_status,
            'start_time': instance.start_time,
            'end_time': instance.end_time,
            'address': instance.address,
            'notes': instance.notes,
            'calendar_id': instance.calendar.ghl_calendar_id if instance.calendar else None,
            'ghl_contact_id': instance.ghl_contact_id,
            'assigned_user': instance.assigned_user,
            'ghl_assigned_user_id': instance.ghl_assigned_user_id,
        }
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        
        # Set flag to skip signal sync before saving (to prevent loop)
        instance._skip_ghl_sync = True
        
        # Save the appointment
        self.perform_update(serializer)
        
        # Refresh instance from database to get updated values
        updated_instance = serializer.instance
        updated_instance.refresh_from_db()
        
        # Detect changed fields
        changed_fields = {}
        for field, old_value in previous_fields.items():
            new_value = getattr(updated_instance, field, None)
            if old_value != new_value:
                changed_fields[field] = new_value
        
        # Sync to GHL if there are changes and appointment has a GHL ID
        if changed_fields and updated_instance.ghl_appointment_id:
            # Skip signal sync to prevent loop (already set above, but ensure it's still set)
            updated_instance._skip_ghl_sync = True
            from .ghl_appointment_sync import update_appointment_in_ghl
            update_appointment_in_ghl(updated_instance, changed_fields=changed_fields)
        
        if getattr(updated_instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            updated_instance._prefetched_objects_cache = {}
        
        return Response(serializer.data)

    def perform_update(self, serializer):
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        """Delete appointment and sync deletion to GHL"""
        instance = self.get_object()
        
        # Sync deletion to GHL before deleting from database
        if instance.ghl_appointment_id and not instance.ghl_appointment_id.startswith('local_'):
            # Skip signal sync to prevent loop
            instance._skip_ghl_sync = True
            from .ghl_appointment_sync import delete_appointment_from_ghl
            delete_appointment_from_ghl(instance)
        
        # Delete from database
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
    GET: Returns jobs for a specific series.
    DELETE: Deletes all jobs in a series (admin only).
    
    Query params (for GET):
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

    def delete(self, request, series_id):
        """
        Delete all jobs in a series.
        Users can delete if they are assigned to any job in the series, or if they are admins.
        """
        user = request.user
        if not user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=401)
        
        # Get all jobs in the series
        jobs_in_series = Job.objects.filter(series_id=series_id)
        job_count = jobs_in_series.count()
        
        if job_count == 0:
            return Response({
                'detail': f'No jobs found for series {series_id}.',
                'series_id': str(series_id),
                'deleted_count': 0
            }, status=404)
        
        # Check permissions: user must be admin OR assigned to at least one job in the series
        is_admin = getattr(user, 'is_admin', False)
        if not is_admin:
            # Check if user is assigned to any job in this series
            user_assigned_jobs = jobs_in_series.filter(assignments__user=user).distinct()
            if not user_assigned_jobs.exists():
                return Response({
                    'detail': 'You do not have permission to delete this job series. You must be assigned to at least one job in the series or be an admin.'
                }, status=403)
        
        # Get job IDs for related record cleanup
        job_ids = list(jobs_in_series.values_list('id', flat=True))
        
        # Handle appointments linked to these jobs - delete from GHL and our database
        appointments_to_delete = Appointment.objects.filter(job_id__in=job_ids)
        appointment_count = appointments_to_delete.count()
        appointments_deleted_from_ghl = 0
        appointments_deleted_from_db = 0
        
        if appointment_count > 0:
            print(f"Found {appointment_count} appointment(s) linked to jobs in series {series_id}")
            
            # Delete appointments from GHL first, then from our database
            for appointment in appointments_to_delete:
                try:
                    # Delete from GHL (skip sync flag to prevent signal from interfering)
                    appointment._skip_ghl_sync = True
                    if delete_appointment_from_ghl(appointment):
                        appointments_deleted_from_ghl += 1
                        print(f"✅ Deleted appointment {appointment.ghl_appointment_id} from GHL")
                    else:
                        print(f"⚠️ Failed to delete appointment {appointment.ghl_appointment_id} from GHL, but will still delete from database")
                except Exception as e:
                    print(f"❌ Error deleting appointment {appointment.ghl_appointment_id} from GHL: {str(e)}")
                    # Continue with deletion from database even if GHL deletion fails
                
                # Delete from our database
                try:
                    appointment.delete()
                    appointments_deleted_from_db += 1
                except Exception as e:
                    print(f"❌ Error deleting appointment {appointment.id} from database: {str(e)}")
            
            print(f"Deleted {appointments_deleted_from_db} appointment(s) from database (attempted to delete {appointments_deleted_from_ghl} from GHL)")
        
        # Delete related records first (though CASCADE should handle this, being explicit is safer)
        # Note: JobServiceItem, JobAssignment, and JobOccurrence have CASCADE delete,
        # but we'll delete them explicitly for clarity and to handle any edge cases
        JobServiceItem.objects.filter(job_id__in=job_ids).delete()
        JobAssignment.objects.filter(job_id__in=job_ids).delete()
        JobOccurrence.objects.filter(job_id__in=job_ids).delete()
        
        # Delete all jobs in the series
        jobs_in_series.delete()
        
        return Response({
            'detail': f'Successfully deleted {job_count} job(s) from series {series_id}.',
            'series_id': str(series_id),
            'deleted_count': job_count,
            'appointments_deleted_from_ghl': appointments_deleted_from_ghl,
            'appointments_deleted_from_db': appointments_deleted_from_db
        }, status=200)



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