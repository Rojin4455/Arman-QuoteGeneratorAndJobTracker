from rest_framework import viewsets, permissions
from rest_framework.response import Response
from rest_framework.decorators import action
from django.shortcuts import get_object_or_404
from django.db.models import Q

from .models import Job, JTService, JobOccurrence
from .serializers import JobSerializer, JTServiceSerializer, OccurrenceEventSerializer
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
    - Admins: all occurrences
    - Normal user: only occurrences for jobs assigned to them
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

        qs = JobOccurrence.objects.select_related('job').filter(
            scheduled_at__gte=start_dt,
            scheduled_at__lte=end_dt,
        )

        user = request.user
        if not user.is_authenticated:
            return Response([], status=200)
        if not getattr(user, 'is_admin', False):
            qs = qs.filter(job__assignments__user=user).distinct()

        data = OccurrenceEventSerializer(qs.order_by('scheduled_at', 'sequence'), many=True).data
        return Response(data)


