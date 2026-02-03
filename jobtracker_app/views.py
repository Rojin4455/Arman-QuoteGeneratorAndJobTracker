import json
import uuid
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO

from django.db.models import Count, Sum, Min, Q, Prefetch
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

from accounts.models import Webhook, GHLAuthCredentials, GHLCustomField, Contact, Address
from accounts.utils import (
    get_ghl_media_storage_for_location,
    upload_file_to_ghl_media,
    delete_ghl_media,
)
from service_app.models import User, Appointment
from .models import Job, JobOccurrence, JobServiceItem, JobAssignment, JobImage
from .ghl_appointment_sync import delete_appointment_from_ghl
from .serializers import (
    CalendarEventSerializer,
    JobSeriesCreateSerializer,
    JobSerializer,
    LocationSummarySerializer,
    OccurrenceEventSerializer,
    AppointmentCalendarSerializer,
    AppointmentSerializer,
    JobImageSerializer,
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


def apply_job_filters(queryset, request, skip_assignee_ids=False, allow_to_convert=False):
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
        allow_to_convert: If True, don't exclude jobs with status "to_convert" (useful for DELETE operations)
    """
    params = request.query_params
    
    # Filter by status (supports multiple statuses)
    status = params.get('status')
    if status:
        status_list = [s.strip() for s in status.split(',') if s.strip()]
        if status_list:
            queryset = queryset.filter(status__in=status_list)
    else:
        # Only exclude to_convert if not explicitly allowed
        if not allow_to_convert:
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
    queryset = Job.objects.all().select_related('submission', 'contact', 'address').prefetch_related('images', 'images__uploaded_by')
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

        # Allow to_convert jobs to be accessed for DELETE operations
        allow_to_convert = (self.request.method == 'DELETE')

        if getattr(user, 'is_admin', False):
            # Apply filters for admins
            queryset = apply_job_filters(queryset, self.request, allow_to_convert=allow_to_convert)
            return queryset

        # Normal users: only jobs assigned to them
        queryset = queryset.filter(assignments__user=user).distinct()
        # Apply filters for normal users
        queryset = apply_job_filters(queryset, self.request, allow_to_convert=allow_to_convert)
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
            'appointment',
            'images',
            'images__uploaded_by'
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

    @action(detail=True, methods=['patch'], url_path='update-payment-method')
    def update_payment_method(self, request, pk=None):
        """
        Update payment method for a completed job.
        Only allows updating payment_method for jobs with status 'completed'.
        
        PATCH /api/jobtracker/jobs/{id}/update-payment-method/
        Body: {"payment_method": "cash"}
        """
        job = self.get_object()
        
        # Check if job is completed
        if job.status != 'completed':
            return Response({
                'detail': f'Payment method can only be updated for completed jobs. Current job status: {job.status}'
            }, status=400)
        
        # Get payment_method from request data
        payment_method = request.data.get('payment_method')
        if not payment_method:
            return Response({
                'detail': 'payment_method field is required'
            }, status=400)
        
        # Validate payment_method choice
        valid_methods = [choice[0] for choice in Job.PAYMENT_METHOD_CHOICES]
        if payment_method not in valid_methods:
            return Response({
                'detail': f'Invalid payment_method. Must be one of: {", ".join(valid_methods)}'
            }, status=400)
        
        # Update payment method
        job.payment_method = payment_method
        job.save()
        
        # Update GHL custom field for Payment Method
        try:
            # Get location_id and ghl_contact_id from job
            location_id = None
            ghl_contact_id = None
            
            # Try to get location_id from job's contact
            if job.contact:
                location_id = job.contact.location_id
                ghl_contact_id = job.ghl_contact_id or job.contact.contact_id
            elif job.ghl_contact_id:
                # Fallback: get contact by ghl_contact_id
                contact = Contact.objects.filter(contact_id=job.ghl_contact_id).first()
                if contact:
                    location_id = contact.location_id
                    ghl_contact_id = job.ghl_contact_id
            elif job.submission and job.submission.contact:
                # Fallback: get from submission contact
                location_id = job.submission.contact.location_id
                ghl_contact_id = job.ghl_contact_id or job.submission.contact.contact_id
            
            if not location_id or not ghl_contact_id:
                print("⚠️ [PAYMENT METHOD] Could not resolve location_id or ghl_contact_id, skipping GHL update")
            else:
                # Get GHLAuthCredentials by location_id
                try:
                    credentials = GHLAuthCredentials.objects.get(location_id=location_id)
                except GHLAuthCredentials.DoesNotExist:
                    print(f"❌ [PAYMENT METHOD] No GHLAuthCredentials found for location_id: {location_id}")
                except GHLAuthCredentials.MultipleObjectsReturned:
                    print(f"⚠️ [PAYMENT METHOD] Multiple credentials found for location_id: {location_id}, using first")
                    credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
                else:
                    # Get Payment Method custom field
                    try:
                        payment_method_field = GHLCustomField.objects.get(
                            account=credentials,
                            field_name='Payment Method',
                            is_active=True
                        )
                        
                        # Refresh from database to ensure we have the latest value
                        payment_method_field.refresh_from_db()
                        
                        # Get the actual GHL field ID value
                        ghl_field_id_value = payment_method_field.ghl_field_id
                        
                        # Validate that we have a real field ID (not a placeholder)
                        if not ghl_field_id_value or ghl_field_id_value == 'ghl_field_id' or len(ghl_field_id_value) < 5:
                            print(f"❌ [PAYMENT METHOD] Invalid ghl_field_id value: '{ghl_field_id_value}'. Please check the database.")
                            print(f"   The field ID should be the actual GHL custom field ID, not a placeholder.")
                        else:
                            # Map payment_method to display-friendly value
                            payment_method_display = dict(Job.PAYMENT_METHOD_CHOICES).get(payment_method, payment_method)
                            
                            # Build custom fields payload with the actual field ID
                            custom_fields = [{
                                "id": str(ghl_field_id_value),
                                "field_value": payment_method_display
                            }]

                            print(f"🔍 [PAYMENT METHOD] Field ID: {ghl_field_id_value}")
                            print(f"🔍 [PAYMENT METHOD] Payment Method: {payment_method_display}")
                            print(f"🔍 [PAYMENT METHOD] Payload: {custom_fields}")
                            
                            # Update GHL contact with custom field
                            update_data = {
                                "customFields": custom_fields
                            }
                            
                            url = f'https://services.leadconnectorhq.com/contacts/{ghl_contact_id}'
                            headers = {
                                'Authorization': f'Bearer {credentials.access_token}',
                                'Content-Type': 'application/json',
                                'Version': '2021-07-28',
                                'Accept': 'application/json'
                            }
                            
                            response = requests.put(url, headers=headers, json=update_data)
                            if response.status_code in [200, 201]:
                                print(f"✅ [PAYMENT METHOD] Successfully updated GHL custom field 'Payment Method' to '{payment_method_display}'")
                            else:
                                print(f"❌ [PAYMENT METHOD] Failed to update GHL custom field: {response.status_code} - {response.text}")
                                print(f"   Request URL: {url}")
                                print(f"   Request payload: {update_data}")
                    except GHLCustomField.DoesNotExist:
                        print(f"⚠️ [PAYMENT METHOD] 'Payment Method' custom field not found for location_id: {location_id}")
                    except Exception as e:
                        print(f"❌ [PAYMENT METHOD] Error updating GHL custom field: {str(e)}")
                        import traceback
                        traceback.print_exc()
        except Exception as e:
            print(f"❌ [PAYMENT METHOD] Error in GHL custom field update process: {str(e)}")
            import traceback
            traceback.print_exc()
            # Don't fail the request if GHL update fails, just log the error
        
        serializer = self.get_serializer(job)
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

        data = CalendarEventSerializer(
            qs.select_related('contact').order_by('scheduled_at', 'series_sequence'),
            many=True
        ).data
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
        ).exclude(start_time__isnull=True).select_related('assigned_user', 'contact', 'calendar').prefetch_related('users')
        
        # Exclude appointments with calendar name "Reccuring Service Calendar"
        qs = qs.exclude(calendar__name="Reccuring Service Calendar")
        qs = qs.exclude(calendar__name="FREE On-Site Estimate")
        
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
        ).prefetch_related(
            'users',
            Prefetch('contact__contact_location', queryset=Address.objects.order_by('order')),
        ).all()

        # Exclude recurring service calendar appointments from all actions
        qs = qs.exclude(calendar__name="Reccuring Service Calendar")

        # For list views, also hide estimate appointments from the main list.
        # For retrieve / update / delete, we still want to be able to find
        # estimate appointments by ID so they can be deleted with the same endpoint.
        action = getattr(self, "action", None)
        if action == "list":
            qs = qs.exclude(calendar__name="FREE On-Site Estimate")
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




class JobImageViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing job images.
    Allows uploading images for jobs in any status.
    
    Permissions:
    - Admins: Full access to all job images
    - Normal users: Can only access images for jobs assigned to them
    
    Endpoints:
    - POST /api/jobtracker/job-images/ - Upload a new image (requires job_id in request)
    - GET /api/jobtracker/job-images/ - List all images (filter by job_id query param)
    - GET /api/jobtracker/job-images/{id}/ - Retrieve a specific image
    - DELETE /api/jobtracker/job-images/{id}/ - Delete an image
    """
    serializer_class = JobImageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Filter queryset based on user permissions"""
        user = self.request.user
        
        if not user.is_authenticated:
            return JobImage.objects.none()
        
        qs = JobImage.objects.select_related('job', 'uploaded_by').all()
        
        # Filter by job_id if provided
        job_id = self.request.query_params.get('job_id')
        if job_id:
            try:
                job_uuid = uuid.UUID(job_id)
                qs = qs.filter(job_id=job_uuid)
            except (ValueError, TypeError):
                return JobImage.objects.none()
        
        # Permission filtering
        is_admin = getattr(user, 'is_admin', False)
        if not is_admin:
            # Normal users: only images for jobs assigned to them
            qs = qs.filter(job__assignments__user=user).distinct()
        
        return qs.order_by('-created_at')

    def get_serializer_context(self):
        """Add request to serializer context for building absolute URLs"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_create(self, serializer):
        """Validate job, upload image to GHL only (not S3), then save record with ghl_file_id/ghl_file_url."""
        from rest_framework.exceptions import ValidationError, NotFound

        job_id = self.request.data.get('job')
        if not job_id:
            raise ValidationError({'job': 'job field is required'})

        uploaded_file = self.request.FILES.get('image')
        if not uploaded_file:
            raise ValidationError({'image': 'image file is required'})

        try:
            job = Job.objects.get(id=job_id)
        except Job.DoesNotExist:
            raise NotFound('Job not found')

        user = self.request.user
        is_admin = getattr(user, 'is_admin', False)
        if not is_admin and not job.assignments.filter(user=user).exists():
            raise PermissionDenied("You do not have permission to upload images for this job.")

        location_id = None
        if job.contact:
            location_id = getattr(job.contact, 'location_id', None)
        if not location_id:
            creds = GHLAuthCredentials.objects.first()
            location_id = creds.location_id if creds else None
        if not location_id:
            raise ValidationError({'detail': 'No GHL location available for media upload.'})

        credentials, media_storage = get_ghl_media_storage_for_location(location_id, storage_name='Job Images')
        if not credentials or not media_storage:
            raise ValidationError({'detail': 'GHL media storage not configured for this location.'})

        name = self.request.data.get('caption') or getattr(uploaded_file, 'name', 'job-image')
        if isinstance(name, str) and '/' in name:
            name = name.split('/')[-1]
        file_ref = BytesIO(uploaded_file.read())
        file_ref.name = name
        result = upload_file_to_ghl_media(
            credentials.access_token,
            location_id,
            media_storage.ghl_id,
            name,
            file_ref,
            file_content_type=getattr(uploaded_file, 'content_type', None),
        )
        if not result:
            raise ValidationError({'detail': 'Failed to upload image to GHL media.'})

        serializer.save(
            uploaded_by=user,
            image=None,
            ghl_file_id=result.get('fileId'),
            ghl_file_url=result.get('url'),
        )


    def get_object(self):
        """Override to check permissions on individual object"""
        obj = super().get_object()
        user = self.request.user
        
        # Admins can access any image
        if getattr(user, 'is_admin', False):
            return obj
        
        # Normal users: only if assigned to the job
        if not obj.job.assignments.filter(user=user).exists():
            raise PermissionDenied("You do not have permission to access this image.")
        
        return obj

    def perform_destroy(self, instance):
        """Delete from GHL media if we have ghl_file_id, then delete local record."""
        if instance.ghl_file_id:
            location_id = None
            if instance.job.contact:
                location_id = getattr(instance.job.contact, 'location_id', None)
            if not location_id:
                creds = GHLAuthCredentials.objects.first()
                location_id = creds.location_id if creds else None
            if location_id:
                try:
                    creds = GHLAuthCredentials.objects.filter(location_id=location_id).first()
                    if creds:
                        delete_ghl_media(creds.access_token, instance.ghl_file_id, location_id)
                except Exception:
                    pass
        instance.delete()


class EstimateAppointmentListView(APIView):
    """
    Get all estimate appointments (appointments with calendar name "FREE On-Site Estimate").
    Returns all matching records (no pagination).
    
    Query params:
    - status: filter by estimate_status (comma-separated list)
    - assigned_user_ids: filter by assigned user IDs (comma-separated list) [REQUIRED]
    - start or start_date: filter by start_time >= (ISO datetime or YYYY-MM-DD)
    - end or end_date: filter by start_time < (ISO datetime or YYYY-MM-DD; end_date is exclusive)
    - search: search in title and notes (case-insensitive)
    
    Permissions:
    - Admins: Full access to all estimate appointments
    - Normal users: Only estimate appointments assigned to them or where they are in users list
    """
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        
        # Check if assigned_user_ids is provided - if not, return empty result
        assigned_user_ids = request.query_params.get('assigned_user_ids')
        if not assigned_user_ids:
            return Response([], status=200)
        
        # Filter by calendar name "FREE On-Site Estimate"
        qs = Appointment.objects.filter(
            calendar__name="FREE On-Site Estimate"
        ).select_related(
            'assigned_user', 'contact', 'calendar'
        ).prefetch_related(
            'users',
            Prefetch('contact__contact_location', queryset=Address.objects.order_by('order')),
        ).all()
        
        is_admin = getattr(user, 'is_admin', False)
        
        # Permission filtering
        if not is_admin:
            # Normal users: only appointments assigned to them or where they are in users list
            qs = qs.filter(
                Q(assigned_user=user) | Q(users=user)
            ).distinct()
        
        # Filter by estimate_status (comma-separated list)
        status = request.query_params.get('status')
        if status:
            status_list = [s.strip() for s in status.split(',') if s.strip()]
            if status_list:
                qs = qs.filter(estimate_status__in=status_list)
        
        # Filter by assigned_user_ids (comma-separated list of IDs, UUIDs, or emails)
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
        
        # Filter by date range (support start/end ISO or start_date/end_date YYYY-MM-DD)
        start_param = request.query_params.get('start') or request.query_params.get('start_date')
        end_param = request.query_params.get('end') or request.query_params.get('end_date')
        if start_param:
            start_dt = parse_datetime(start_param)
            if not start_dt and len(start_param) >= 10:
                try:
                    start_dt = datetime.strptime(start_param[:10], '%Y-%m-%d')
                except ValueError:
                    pass
            if start_dt:
                qs = qs.filter(start_time__gte=start_dt)
        if end_param:
            end_dt = parse_datetime(end_param)
            if not end_dt and len(end_param) >= 10:
                try:
                    end_dt = datetime.strptime(end_param[:10], '%Y-%m-%d') + timedelta(days=1)
                except ValueError:
                    pass
            if end_dt:
                qs = qs.filter(start_time__lt=end_dt)
        
        # Search filter (title and notes)
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(notes__icontains=search)
            )
        
        # Order by start_time
        qs = qs.order_by('-start_time', '-created_at')
        
        # Return all records (no pagination)
        serializer = AppointmentSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data, status=200)


class EstimateAppointmentUpdateStatusView(APIView):
    """
    Update estimate_status for an estimate appointment.
    
    PATCH /api/jobtracker/estimate-appointments/{id}/update-status/
    Body: {"estimate_status": "confirmed"}
    
    Permissions:
    - Admins: Can update any estimate appointment
    - Normal users: Can only update estimate appointments assigned to them or where they are in users list
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, appointment_id):
        try:
            appointment = Appointment.objects.select_related('calendar', 'assigned_user', 'contact').prefetch_related(
                'users',
                Prefetch('contact__contact_location', queryset=Address.objects.order_by('order')),
            ).get(id=appointment_id)
        except Appointment.DoesNotExist:
            return Response({'detail': 'Appointment not found.'}, status=404)
        
        # Check if it's an estimate appointment
        if not appointment.calendar or appointment.calendar.name != "FREE On-Site Estimate":
            return Response({
                'detail': 'This endpoint is only for estimate appointments (FREE On-Site Estimate calendar).'
            }, status=400)
        
        # Check permissions
        user = request.user
        is_admin = getattr(user, 'is_admin', False)
        if not is_admin:
            # Normal users: only if assigned to them or in users list
            if appointment.assigned_user != user and user not in appointment.users.all():
                raise PermissionDenied("You don't have permission to update this appointment.")
        
        # Get estimate_status from request data
        estimate_status = request.data.get('estimate_status')
        if not estimate_status:
            return Response({
                'detail': 'estimate_status field is required'
            }, status=400)
        
        # Validate estimate_status choice
        valid_statuses = [choice[0] for choice in Appointment.ESTIMATE_STATUS_CHOICES]
        if estimate_status not in valid_statuses:
            return Response({
                'detail': f'Invalid estimate_status. Must be one of: {", ".join(valid_statuses)}'
            }, status=400)
        
        # Update estimate_status
        appointment.estimate_status = estimate_status
        appointment.save()
        
        # Update GHL custom field for Estimate Status
        try:
            # Get contact to find location_id
            location_id = None
            ghl_contact_id = None
            
            # Try to get location_id from appointment's contact
            if appointment.contact:
                location_id = appointment.contact.location_id
                ghl_contact_id = appointment.ghl_contact_id or appointment.contact.contact_id
            elif appointment.ghl_contact_id:
                # Fallback: get contact by ghl_contact_id
                contact = Contact.objects.filter(contact_id=appointment.ghl_contact_id).first()
                if contact:
                    location_id = contact.location_id
                    ghl_contact_id = appointment.ghl_contact_id
            
            if not location_id or not ghl_contact_id:
                print("⚠️ [ESTIMATE STATUS] Could not resolve location_id or ghl_contact_id, skipping GHL update")
            else:
                # Get GHLAuthCredentials by location_id
                try:
                    credentials = GHLAuthCredentials.objects.get(location_id=location_id)
                except GHLAuthCredentials.DoesNotExist:
                    print(f"❌ [ESTIMATE STATUS] No GHLAuthCredentials found for location_id: {location_id}")
                except GHLAuthCredentials.MultipleObjectsReturned:
                    print(f"⚠️ [ESTIMATE STATUS] Multiple credentials found for location_id: {location_id}, using first")
                    credentials = GHLAuthCredentials.objects.filter(location_id=location_id).first()
                else:
                    # Get Estimate Status custom field
                    try:
                        estimate_status_field = GHLCustomField.objects.get(
                            account=credentials,
                            field_name='Estimate Status',
                            is_active=True
                        )
                        
                        # Refresh from database to ensure we have the latest value
                        estimate_status_field.refresh_from_db()
                        
                        # Get the actual GHL field ID value
                        ghl_field_id_value = estimate_status_field.ghl_field_id
                        
                        # Validate that we have a real field ID (not a placeholder)
                        if not ghl_field_id_value or ghl_field_id_value == 'ghl_field_id' or len(ghl_field_id_value) < 5:
                            print(f"❌ [ESTIMATE STATUS] Invalid ghl_field_id value: '{ghl_field_id_value}'. Please check the database.")
                            print(f"   The field ID should be the actual GHL custom field ID, not a placeholder.")
                            return
                        
                        # Map estimate_status to display-friendly value
                        status_display = dict(Appointment.ESTIMATE_STATUS_CHOICES).get(estimate_status, estimate_status)
                        
                        # Build custom fields payload with the actual field ID
                        custom_fields = [{
                            "id": str(ghl_field_id_value),
                            "field_value": status_display
                        }]

                        print(f"🔍 [ESTIMATE STATUS] Field ID: {ghl_field_id_value}")
                        print(f"🔍 [ESTIMATE STATUS] Status: {status_display}")
                        print(f"🔍 [ESTIMATE STATUS] Payload: {custom_fields}")
                        
                        # Update GHL contact with custom field
                        update_data = {
                            "customFields": custom_fields
                        }
                        
                        url = f'https://services.leadconnectorhq.com/contacts/{ghl_contact_id}'
                        headers = {
                            'Authorization': f'Bearer {credentials.access_token}',
                            'Content-Type': 'application/json',
                            'Version': '2021-07-28',
                            'Accept': 'application/json'
                        }
                        
                        response = requests.put(url, headers=headers, json=update_data)
                        if response.status_code in [200, 201]:
                            print(f"✅ [ESTIMATE STATUS] Successfully updated GHL custom field 'Estimate Status' to '{status_display}'")
                        else:
                            print(f"❌ [ESTIMATE STATUS] Failed to update GHL custom field: {response.status_code} - {response.text}")
                            print(f"   Request URL: {url}")
                            print(f"   Request payload: {update_data}")
                    except GHLCustomField.DoesNotExist:
                        print(f"⚠️ [ESTIMATE STATUS] 'Estimate Status' custom field not found for location_id: {location_id}")
                    except Exception as e:
                        print(f"❌ [ESTIMATE STATUS] Error updating GHL custom field: {str(e)}")
        except Exception as e:
            print(f"❌ [ESTIMATE STATUS] Error in GHL custom field update process: {str(e)}")
            # Don't fail the request if GHL update fails, just log the error
        
        serializer = AppointmentSerializer(appointment, context={'request': request})
        return Response(serializer.data)


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