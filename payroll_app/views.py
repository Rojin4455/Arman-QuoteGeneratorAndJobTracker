from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Q, Sum, Count
from django.utils import timezone
from decimal import Decimal
from datetime import datetime, timedelta

from service_app.models import User
from .models import (
    EmployeeProfile, CollaborationRate, TimeEntry, Payout, PayrollSettings
)
from .serializers import (
    EmployeeProfileSerializer, CollaborationRateCreateSerializer,
    TimeEntrySerializer, PayoutSerializer, PayrollSettingsSerializer
)


class IsAdminOrEmployeePermission(permissions.BasePermission):
    """Permission for admin or employee access"""
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class IsAdminOnlyPermission(permissions.BasePermission):
    """Permission for admin only"""
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            getattr(request.user, 'is_admin', False)
        )


class EmployeeProfileViewSet(viewsets.ModelViewSet):
    """ViewSet for managing employee profiles"""
    queryset = EmployeeProfile.objects.all().select_related('user').prefetch_related('user__collaboration_rates')
    serializer_class = EmployeeProfileSerializer
    permission_classes = [IsAdminOnlyPermission]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Search functionality
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(user__email__icontains=search) |
                Q(phone__icontains=search) |
                Q(department__icontains=search) |
                Q(position__icontains=search)
            )
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by pay scale type
        pay_scale = self.request.query_params.get('pay_scale_type', None)
        if pay_scale:
            queryset = queryset.filter(pay_scale_type=pay_scale)
        
        return queryset
    
    @action(detail=True, methods=['post'], url_path='collaboration-rates')
    def update_collaboration_rates(self, request, pk=None):
        """Update collaboration rates for an employee"""
        employee_profile = self.get_object()
        employee = employee_profile.user
        
        # Delete existing rates
        CollaborationRate.objects.filter(employee=employee).delete()
        
        # Create new rates
        rates_data = request.data.get('rates', [])
        created_rates = []
        for rate_data in rates_data:
            rate_data['employee'] = employee.id
            serializer = CollaborationRateCreateSerializer(data=rate_data)
            if serializer.is_valid():
                rate = serializer.save(employee=employee)
                created_rates.append(rate)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'message': 'Collaboration rates updated successfully',
            'rates': CollaborationRateCreateSerializer(created_rates, many=True).data
        })


class TimeEntryViewSet(viewsets.ModelViewSet):
    """ViewSet for time clock entries"""
    queryset = TimeEntry.objects.all().select_related('employee')
    serializer_class = TimeEntrySerializer
    permission_classes = [IsAdminOrEmployeePermission]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        
        # Admins see all, employees see only their own
        if not getattr(user, 'is_admin', False):
            queryset = queryset.filter(employee=user)
        
        # Filter by date
        date = self.request.query_params.get('date', None)
        if date:
            try:
                date_obj = datetime.strptime(date, '%Y-%m-%d').date()
                queryset = queryset.filter(
                    check_in_time__date=date_obj
                )
            except ValueError:
                pass
        
        # Filter by employee (admin only)
        employee_id = self.request.query_params.get('employee', None)
        if employee_id and getattr(user, 'is_admin', False):
            queryset = queryset.filter(employee_id=employee_id)
        
        return queryset.order_by('-check_in_time')
    
    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        """Check in for hourly employees"""
        user = request.user
        
        # Check if user has active session
        active_entry = TimeEntry.objects.filter(
            employee=user,
            status='checked_in'
        ).first()
        
        if active_entry:
            return Response({
                'error': 'You already have an active session. Please check out first.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create new time entry
        entry = TimeEntry.objects.create(
            employee=user,
            check_in_time=timezone.now(),
            notes=request.data.get('notes', '')
        )
        
        serializer = self.get_serializer(entry)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'], url_path='active-session')
    def active_session(self, request):
        """Get active check-in session for current user"""
        user = request.user
        active_entry = TimeEntry.objects.filter(
            employee=user,
            status='checked_in'
        ).first()
        
        if not active_entry:
            return Response({'active': False})
        
        serializer = self.get_serializer(active_entry)
        elapsed_time = None
        if active_entry.check_in_time:
            delta = timezone.now() - active_entry.check_in_time
            elapsed_time = Decimal(str(delta.total_seconds() / 3600)).quantize(Decimal('0.01'))
        
        return Response({
            'active': True,
            'entry': serializer.data,
            'elapsed_hours': float(elapsed_time) if elapsed_time else 0
        })
    
    @action(detail=True, methods=['post'], url_path='check-out')
    def check_out(self, request, pk=None):
        """Check out and complete time entry"""
        entry = get_object_or_404(TimeEntry, pk=pk)
        
        # Verify ownership (unless admin)
        user = request.user
        if not getattr(user, 'is_admin', False) and entry.employee != user:
            return Response({
                'error': 'You can only check out your own time entries.'
            }, status=status.HTTP_403_FORBIDDEN)
        
        if entry.status == 'checked_out':
            return Response({
                'error': 'This time entry is already checked out.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Update entry
        entry.check_out_time = timezone.now()
        entry.total_hours = entry.calculate_hours()
        entry.status = 'checked_out'
        if 'notes' in request.data:
            entry.notes = request.data.get('notes', entry.notes)
        entry.save()
        
        # Create hourly payout if employee has hourly rate
        try:
            profile = entry.employee.employee_profile
            if profile.pay_scale_type == 'hourly' and profile.hourly_rate and entry.total_hours:
                amount = (entry.total_hours * profile.hourly_rate).quantize(Decimal('0.01'))
                Payout.objects.create(
                    employee=entry.employee,
                    payout_type='hourly',
                    amount=amount,
                    time_entry=entry,
                    notes=f"Hourly payout for {entry.total_hours} hours"
                )
        except EmployeeProfile.DoesNotExist:
            pass
        
        serializer = self.get_serializer(entry)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='today')
    def today_entries(self, request):
        """Get today's time entries for current user"""
        user = request.user
        today = timezone.now().date()
        
        queryset = self.get_queryset().filter(
            employee=user,
            check_in_time__date=today
        )
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class PayoutViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for viewing payouts (read-only, create via calculator)"""
    queryset = Payout.objects.all().select_related('employee', 'job', 'time_entry')
    serializer_class = PayoutSerializer
    permission_classes = [IsAdminOrEmployeePermission]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        
        # Admins see all, employees see only their own
        if not getattr(user, 'is_admin', False):
            queryset = queryset.filter(employee=user)
        
        # Filter by employee (admin only)
        employee_id = self.request.query_params.get('employee', None)
        if employee_id and getattr(user, 'is_admin', False):
            queryset = queryset.filter(employee_id=employee_id)
        
        # Filter by payout type
        payout_type = self.request.query_params.get('type', None)
        if payout_type:
            queryset = queryset.filter(payout_type=payout_type)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date', None)
        end_date = self.request.query_params.get('end_date', None)
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                queryset = queryset.filter(created_at__gte=start)
            except ValueError:
                pass
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                queryset = queryset.filter(created_at__lt=end)
            except ValueError:
                pass
        
        # Filter by project title (for manual entries)
        project_title = self.request.query_params.get('project_title', None)
        if project_title:
            queryset = queryset.filter(project_title__icontains=project_title)
        
        return queryset.order_by('-created_at')


class CalculatorView(APIView):
    """Manual calculator for creating payouts"""
    permission_classes = [IsAdminOrEmployeePermission]
    
    def post(self, request):
        """Create manual payout entry"""
        payout_type = request.data.get('payout_type')  # 'hourly' or 'project'
        employee_id = request.data.get('employee')
        amount = request.data.get('amount')
        notes = request.data.get('notes', '')
        
        if not all([payout_type, employee_id, amount]):
            return Response({
                'error': 'payout_type, employee, and amount are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            employee = User.objects.get(pk=employee_id)
        except User.DoesNotExist:
            return Response({
                'error': 'Employee not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Verify ownership (unless admin)
        user = request.user
        if not getattr(user, 'is_admin', False) and employee != user:
            return Response({
                'error': 'You can only create payouts for yourself.'
            }, status=status.HTTP_403_FORBIDDEN)
        
        payout_data = {
            'employee': employee,
            'payout_type': payout_type,
            'amount': Decimal(str(amount)),
            'notes': notes
        }
        
        if payout_type == 'hourly':
            hours = request.data.get('hours')
            if not hours:
                return Response({
                    'error': 'hours is required for hourly payout'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get hourly rate from profile
            try:
                profile = employee.employee_profile
                if profile.pay_scale_type != 'hourly' or not profile.hourly_rate:
                    return Response({
                        'error': 'Employee is not configured for hourly payouts'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                calculated_amount = (Decimal(str(hours)) * profile.hourly_rate).quantize(Decimal('0.01'))
                payout_data['amount'] = calculated_amount
                payout_data['notes'] = f"Manual hourly entry: {hours} hours × ${profile.hourly_rate} = ${calculated_amount}"
            except EmployeeProfile.DoesNotExist:
                return Response({
                    'error': 'Employee profile not found'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        elif payout_type == 'project':
            project_value = request.data.get('project_value')
            project_title = request.data.get('project_title', '')
            
            if not project_value:
                return Response({
                    'error': 'project_value is required for project payout'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get rate from profile
            try:
                profile = employee.employee_profile
                if profile.pay_scale_type != 'project':
                    return Response({
                        'error': 'Employee is not configured for project payouts'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # For manual entries, use the provided amount or calculate from rate
                if not amount:
                    # Try to get rate from collaboration rates (default to solo)
                    try:
                        rate = CollaborationRate.objects.get(
                            employee=employee,
                            member_count=1
                        ).percentage
                    except CollaborationRate.DoesNotExist:
                        return Response({
                            'error': 'Employee does not have a rate configured'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    calculated_amount = (Decimal(str(project_value)) * rate / Decimal('100')).quantize(Decimal('0.01'))
                    payout_data['amount'] = calculated_amount
                    payout_data['rate_percentage'] = rate
                else:
                    payout_data['amount'] = Decimal(str(amount))
                
                payout_data['project_value'] = Decimal(str(project_value))
                payout_data['project_title'] = project_title
            except EmployeeProfile.DoesNotExist:
                return Response({
                    'error': 'Employee profile not found'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        payout = Payout.objects.create(**payout_data)
        serializer = PayoutSerializer(payout)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ReportsView(APIView):
    """Reports view combining time entries and payouts"""
    permission_classes = [IsAdminOrEmployeePermission]
    
    def get(self, request):
        """Get combined reports"""
        user = request.user
        is_admin = getattr(user, 'is_admin', False)
        
        # Get filters
        employee_id = request.query_params.get('employee', None)
        payout_type = request.query_params.get('type', None)
        start_date = request.query_params.get('start_date', None)
        end_date = request.query_params.get('end_date', None)
        project_title = request.query_params.get('project_title', None)
        
        # Determine which employee to filter
        if is_admin and employee_id:
            employee_filter = User.objects.filter(pk=employee_id)
        elif not is_admin:
            employee_filter = User.objects.filter(pk=user.id)
        else:
            employee_filter = User.objects.all()
        
        # Get payouts
        payouts_query = Payout.objects.filter(employee__in=employee_filter)
        
        if payout_type:
            payouts_query = payouts_query.filter(payout_type=payout_type)
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                payouts_query = payouts_query.filter(created_at__gte=start)
            except ValueError:
                pass
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                payouts_query = payouts_query.filter(created_at__lt=end)
            except ValueError:
                pass
        if project_title:
            payouts_query = payouts_query.filter(
                Q(project_title__icontains=project_title) |
                Q(job__title__icontains=project_title)
            )
        
        payouts = payouts_query.select_related('employee', 'job', 'time_entry')
        
        # Get time entries (for hourly employees)
        time_entries_query = TimeEntry.objects.filter(
            employee__in=employee_filter,
            status='checked_out'
        )
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d')
                time_entries_query = time_entries_query.filter(check_in_time__gte=start)
            except ValueError:
                pass
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                time_entries_query = time_entries_query.filter(check_in_time__lt=end)
            except ValueError:
                pass
        
        time_entries = time_entries_query.select_related('employee')
        
        # Calculate totals
        total_earnings = payouts.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Serialize data
        payout_serializer = PayoutSerializer(payouts, many=True)
        time_entry_serializer = TimeEntrySerializer(time_entries, many=True)
        
        return Response({
            'payouts': payout_serializer.data,
            'time_entries': time_entry_serializer.data,
            'total_earnings': float(total_earnings),
            'payout_count': payouts.count(),
            'time_entry_count': time_entries.count()
        })


class PayrollSettingsViewSet(viewsets.ModelViewSet):
    """ViewSet for payroll settings (admin only)"""
    queryset = PayrollSettings.objects.all()
    serializer_class = PayrollSettingsSerializer
    permission_classes = [IsAdminOnlyPermission]
    
    def get_object(self):
        """Always return the singleton instance"""
        return PayrollSettings.get_settings()
    
    def list(self, request, *args, **kwargs):
        """Return the singleton instance as a list"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response([serializer.data])
    
    def retrieve(self, request, *args, **kwargs):
        """Return the singleton instance"""
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
