from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny,IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Q, Sum, Count
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta, time
import pytz

from django_filters import rest_framework as filters

from jobtracker_app.models import Job
from accounts.models import Contact
from quote_app.models import CustomerSubmission

from .models import Invoice, InvoiceItem
from .serializers import InvoiceSerializer, InvoiceDetailSerializer, InvoiceItemSerializer
from .services.invoice_sync import sync_invoices


class InvoiceFilter(filters.FilterSet):
    """Filter class for Invoice model"""
    
    search = filters.CharFilter(method='filter_search')
    # Extended choices to include calculated statuses (due, overdue)
    status_choices = list(Invoice.STATUS_CHOICES) + [('due', 'Due'), ('overdue', 'Overdue')]
    status = filters.MultipleChoiceFilter(method='filter_status', choices=status_choices)
    
    issue_date_from = filters.DateTimeFilter(field_name='issue_date', lookup_expr='gte')
    issue_date_to = filters.DateTimeFilter(field_name='issue_date', lookup_expr='lte')
    due_date_from = filters.DateTimeFilter(field_name='due_date', lookup_expr='gte')
    due_date_to = filters.DateTimeFilter(field_name='due_date', lookup_expr='lte')
    created_date_from = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_date_to = filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    
    total_min = filters.NumberFilter(field_name='total', lookup_expr='gte')
    total_max = filters.NumberFilter(field_name='total', lookup_expr='lte')
    amount_due_min = filters.NumberFilter(field_name='amount_due', lookup_expr='gte')
    amount_due_max = filters.NumberFilter(field_name='amount_due', lookup_expr='lte')
    
    contact_id = filters.CharFilter(field_name='contact_id')
    contact_email = filters.CharFilter(field_name='contact_email', lookup_expr='icontains')
    contact_name = filters.CharFilter(field_name='contact_name', lookup_expr='icontains')
    
    location_id = filters.CharFilter(field_name='location_id')
    company_id = filters.CharFilter(field_name='company_id')
    
    is_overdue = filters.BooleanFilter(method='filter_overdue')
    is_paid = filters.BooleanFilter(method='filter_paid')
    has_balance = filters.BooleanFilter(method='filter_has_balance')
    
    class Meta:
        model = Invoice
        fields = ['status', 'location_id', 'company_id', 'contact_id', 'invoice_number', 'currency']
    
    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(invoice_number__icontains=value) |
            Q(name__icontains=value) |
            Q(contact_name__icontains=value) |
            Q(contact_email__icontains=value) |
            Q(contact_phone__icontains=value)
        )
    
    def filter_status(self, queryset, name, value):
        """
        Custom status filter that handles both database statuses and calculated statuses (due, overdue).
        """
        from django.utils import timezone
        now = timezone.now()
        
        if not value:
            return queryset
        
        # Handle multiple status values
        status_filters = Q()
        has_due = False
        has_overdue = False
        regular_statuses = []
        
        for status_val in value:
            if status_val == 'due':
                has_due = True
            elif status_val == 'overdue':
                has_overdue = True
            else:
                regular_statuses.append(status_val)
        
        # Build the combined filter
        if regular_statuses:
            status_filters |= Q(status__in=regular_statuses)
        
        if has_due:
            # Due: invoices with due_date >= today, amount_due > 0, status not paid/void
            status_filters |= Q(
                due_date__gte=now,
                amount_due__gt=0,
            ) & ~Q(status__in=['paid', 'void'])
        
        if has_overdue:
            # Overdue: invoices with due_date < today, amount_due > 0, status not paid/void
            status_filters |= Q(
                due_date__lt=now,
                amount_due__gt=0,
            ) & ~Q(status__in=['paid', 'void'])
        
        return queryset.filter(status_filters)
    
    def filter_overdue(self, queryset, name, value):
        from django.utils import timezone
        if value:
            return queryset.filter(
                due_date__lt=timezone.now(),
                amount_due__gt=0
            ).exclude(status__in=['paid', 'void'])
        return queryset.exclude(due_date__lt=timezone.now(), amount_due__gt=0)
    
    def filter_paid(self, queryset, name, value):
        if value:
            return queryset.filter(status='paid', amount_due=0)
        return queryset.exclude(status='paid')
    
    def filter_has_balance(self, queryset, name, value):
        if value:
            return queryset.filter(amount_due__gt=0)
        return queryset.filter(amount_due=0)


class InvoiceViewSet(viewsets.ModelViewSet):
    """ViewSet for Invoice model"""
    queryset = Invoice.objects.all().prefetch_related('items')
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = InvoiceFilter
    ordering_fields = ['created_at', 'updated_at', 'issue_date', 'due_date', 'total', 'amount_due', 'invoice_number', 'status']
    ordering = ['-created_at']
    search_fields = ['invoice_number', 'contact_name', 'contact_email']
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return InvoiceDetailSerializer
        return InvoiceSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if hasattr(user, 'location_id') and user.location_id:
            queryset = queryset.filter(location_id=user.location_id)
        return queryset
    
    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Sync invoices from GHL API"""

        print("triggered here")
        location_id = request.data.get('location_id')
        invoice_id = request.data.get('invoice_id')
        
        if not location_id:
            return Response({'error': 'location_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            result = sync_invoices(location_id, invoice_id)
            
            if invoice_id:
                if result:
                    serializer = self.get_serializer(result)
                    return Response({'message': 'Invoice synced successfully', 'invoice': serializer.data})
                else:
                    return Response({'error': 'Failed to sync invoice'}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'message': 'Invoices synced successfully', 'statistics': result})
        
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': f'Sync failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get invoice statistics"""
        queryset = self.filter_queryset(self.get_queryset())
        
        location_id = request.query_params.get('location_id')
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        
        date_from = request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__gte=parse_datetime(date_from))
        
        date_to = request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__lte=parse_datetime(date_to))
        
        stats = queryset.aggregate(
            total_invoices=Count('id'),
            total_amount=Sum('total'),
            total_paid=Sum('amount_paid'),
            total_due=Sum('amount_due')
        )
        
        status_breakdown = {}
        for choice_value, choice_label in Invoice.STATUS_CHOICES:
            count = queryset.filter(status=choice_value).count()
            status_breakdown[choice_value] = {'count': count, 'label': choice_label}
        
        from django.utils import timezone
        overdue_count = queryset.filter(
            due_date__lt=timezone.now(),
            amount_due__gt=0
        ).exclude(status__in=['paid', 'void']).count()
        
        return Response({
            'statistics': stats,
            'status_breakdown': status_breakdown,
            'overdue_count': overdue_count
        })


    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """
        Comprehensive invoice analytics endpoint.
        Returns summarized and trend data (daily/weekly/monthly).
        """
        queryset = self.filter_queryset(self.get_queryset())

        central_tz = pytz.timezone("America/Chicago")

        # === Query Params ===
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        granularity = request.query_params.get("granularity", "daily")  # daily | weekly | monthly
        location_id = request.query_params.get("location_id")

        if location_id:
            queryset = queryset.filter(location_id=location_id)

        if start_date:
            start_date = parse_datetime(start_date)
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            end_date = parse_datetime(end_date)
            queryset = queryset.filter(created_at__lte=end_date)
        else:
            end_date = timezone.now().astimezone(central_tz)

        # === Base Stats ===
        total_invoices = queryset.count()
        total_amount = queryset.aggregate(Sum("total"))["total__sum"] or 0
        total_paid = queryset.aggregate(Sum("amount_paid"))["amount_paid__sum"] or 0
        total_due = queryset.aggregate(Sum("amount_due"))["amount_due__sum"] or 0

        overdue_qs = queryset.filter(
            # due_date__lt=timezone.now().astimezone(central_tz),
            amount_due__gt=0,
            status__in=['overdue']
        )
        # .exclude(status__in=["paid", "void"])
        overdue_count = overdue_qs.count()
        overdue_total = overdue_qs.aggregate(Sum("amount_due"))["amount_due__sum"] or 0

        # === Paid vs Unpaid ===
        paid_count = queryset.filter(status="paid").count()
        unpaid_count = queryset.exclude(status="paid").count()

        paid_total = queryset.filter(status="paid").aggregate(Sum("total"))["total__sum"] or 0
        unpaid_total = queryset.exclude(status__in=["paid","payment_processing","draft"]).aggregate(Sum("total"))["total__sum"] or 0

        # === Status Distribution ===
        status_distribution = {}
        now = timezone.now().astimezone(central_tz)
        
        # Calculate Due and Overdue dynamically based on due_date
        # Due: invoices with due_date >= today and amount_due > 0, status not paid/void
        # Include all statuses except 'paid' and 'void' (e.g., sent, partially_paid, payment_processing, draft)
        due_queryset = queryset.filter(
            due_date__gte=now,
            amount_due__gt=0,
            status__in=['sent']
        ).exclude(status__in=['paid', 'void', 'overdue', 'partially_paid', 'partial','payment_processing','draft'])
        due_count = due_queryset.count()

        for due in due_queryset:
            print(due.invoice_number)
        due_total = due_queryset.aggregate(Sum("amount_due"))["amount_due__sum"] or 0
        status_distribution["due"] = {
            "label": "Due",
            "count": due_count,
            "total": due_total,
        }
        
        # Overdue: invoices with due_date < today and amount_due > 0, status not paid/void
        # Include all statuses except 'paid' and 'void'
        overdue_queryset = queryset.filter(
            due_date__lt=now,
            amount_due__gt=0,
            status__in=['sent','overdue']
        )
        # .exclude(status__in=['paid', 'void', 'partially_paid', 'partial'])
        overdue_count = overdue_queryset.count()
        overdue_total = overdue_queryset.aggregate(Sum("amount_due"))["amount_due__sum"] or 0
        status_distribution["overdue"] = {
            "label": "Overdue",
            "count": overdue_count,
            "total": overdue_total,
        }
        
        # Keep other statuses from STATUS_CHOICES (excluding 'overdue' since we calculate it dynamically)
        for value, label in Invoice.STATUS_CHOICES:
            if value != 'overdue':  # Skip 'overdue' as we calculate it dynamically
                count = queryset.filter(status=value).count()
                amount = queryset.filter(status=value).aggregate(Sum("total"))["total__sum"] or 0
                status_distribution[value] = {
                    "label": label,
                    "count": count,
                    "total": amount,
                }

        # === Grouping by Time (Trends) ===
        if granularity == "weekly":
            date_trunc = TruncWeek("created_at")
        elif granularity == "monthly":
            date_trunc = TruncMonth("created_at")
        else:
            date_trunc = TruncDate("created_at")

        trends = (
            queryset.annotate(period=date_trunc)
            .values("period")
            .annotate(
                total_invoices=Count("id"),
                total_amount=Sum("total"),
                total_paid=Sum("amount_paid"),
                total_due=Sum("amount_due"),
                paid_count=Count("id", filter=Q(status="paid")),
                unpaid_count=Count("id", filter=~Q(status="paid")),
            )
            .order_by("period")
        )

        # === Top Customers (by total invoiced) ===
        top_customers = (
            queryset.values("contact_name", "contact_email")
            .annotate(
                total_invoiced=Sum("total"),
                invoices_count=Count("id"),
                total_paid=Sum("amount_paid"),
            )
            .order_by("-total_invoiced")[:5]
        )

        # === Response ===
        return Response({
            "summary": {
                "total_invoices": total_invoices,
                "total_amount": total_amount,
                "total_paid": total_paid,
                "total_due": total_due,
                "overdue_count": overdue_count,
                "overdue_total": overdue_total,
            },
            "paid_unpaid_overview": {
                "paid": {"count": paid_count, "total": paid_total},
                "unpaid": {"count": unpaid_count, "total": unpaid_total},
            },
            "status_distribution": status_distribution,
            "trends": list(trends),
            "top_customers": list(top_customers),
        })
    
    @action(detail=True, methods=['get'])
    def items(self, request, pk=None):
        """Get all items for a specific invoice"""
        invoice = self.get_object()
        items = invoice.items.all()
        serializer = InvoiceItemSerializer(items, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def lead_funnel_report(self, request):
        """
        Lead Funnel Report endpoint.
        Returns comprehensive metrics for the sales funnel including leads, estimates, and jobs.
        """
        user = request.user
        location_id = request.query_params.get('location_id')
        
        # Apply location filter if user has location_id or if location_id is provided
        location_filter = {}
        if hasattr(user, 'location_id') and user.location_id:
            location_filter['location_id'] = user.location_id
        elif location_id:
            location_filter['location_id'] = location_id
        
        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)
        
        # 1. New Lead Count (contacts created in last 7 days)
        contacts_qs = Contact.objects.all()
        if location_filter:
            contacts_qs = contacts_qs.filter(**location_filter)
        new_leads_count = contacts_qs.filter(
            date_added__gte=seven_days_ago
        ).count()
        
        # 2. Open Estimate (submissions with status: draft, responses_completed, packages_selected)
        open_statuses = ['draft', 'responses_completed', 'packages_selected']
        submissions_qs = CustomerSubmission.objects.all()
        if location_filter:
            # Filter by contact's location_id
            submissions_qs = submissions_qs.filter(contact__location_id=location_filter['location_id'])
        open_estimate_count = submissions_qs.filter(status__in=open_statuses).count()
        
        # Get open estimate details with total value
        open_estimates = submissions_qs.filter(status__in=open_statuses)
        open_estimate_total_value = open_estimates.aggregate(Sum('final_total'))['final_total__sum'] or 0
        
        # 3. Rejected Estimate (status: rejected)
        rejected_estimate_count = submissions_qs.filter(status='rejected').count()
        rejected_estimates = submissions_qs.filter(status='rejected')
        rejected_estimate_total_value = rejected_estimates.aggregate(Sum('final_total'))['final_total__sum'] or 0
        
        # 4. Accepted Estimate (status: accepted)
        accepted_estimate_count = submissions_qs.filter(status='accepted').count()
        accepted_estimates = submissions_qs.filter(status='accepted')
        accepted_estimate_total_value = accepted_estimates.aggregate(Sum('final_total'))['final_total__sum'] or 0
        
        # 5. Estimate to Convert (jobs with status: to_convert)
        jobs_qs = Job.objects.all()
        if location_filter:
            # Jobs don't have location_id directly, filter through submissions
            jobs_qs = jobs_qs.filter(submission__contact__location_id=location_filter['location_id'])
        
        estimate_to_convert_jobs_qs = jobs_qs.filter(status='to_convert')
        estimate_to_convert_count = estimate_to_convert_jobs_qs.count()
        estimate_to_convert_total_value = estimate_to_convert_jobs_qs.aggregate(Sum('total_price'))['total_price__sum'] or 0
        
        # 6. Scheduled Job (upcoming jobs, excluding to_convert and cancelled)
        scheduled_jobs_qs = jobs_qs.exclude(status__in=['to_convert', 'cancelled']).filter(
            scheduled_at__gte=now
        )
        scheduled_job_count = scheduled_jobs_qs.count()
        scheduled_job_total_value = scheduled_jobs_qs.aggregate(Sum('total_price'))['total_price__sum'] or 0
        
        # 7. Closed Jobs (status: completed)
        closed_jobs_qs = jobs_qs.filter(status='completed')
        closed_job_count = closed_jobs_qs.count()
        closed_job_total_value = closed_jobs_qs.aggregate(Sum('total_price'))['total_price__sum'] or 0
        
        # Additional metrics for better insights
        # Total pipeline value (open estimates + scheduled jobs)
        pipeline_value = float(open_estimate_total_value) + float(scheduled_job_total_value)
        
        # Conversion rates
        total_estimates = open_estimate_count + rejected_estimate_count + accepted_estimate_count
        acceptance_rate = (accepted_estimate_count / total_estimates * 100) if total_estimates > 0 else 0
        rejection_rate = (rejected_estimate_count / total_estimates * 100) if total_estimates > 0 else 0
        
        return Response({
            'report_period': {
                'start_date': seven_days_ago.isoformat(),
                'end_date': now.isoformat(),
                'period_days': 7
            },
            'lead_funnel': {
                'new_leads': {
                    'count': new_leads_count,
                    'label': 'New Leads (Last 7 Days)'
                },
                'open_estimates': {
                    'count': open_estimate_count,
                    'total_value': float(open_estimate_total_value),
                    'label': 'Open Estimates',
                    'statuses': open_statuses
                },
                'rejected_estimates': {
                    'count': rejected_estimate_count,
                    'total_value': float(rejected_estimate_total_value),
                    'label': 'Rejected Estimates'
                },
                'accepted_estimates': {
                    'count': accepted_estimate_count,
                    'total_value': float(accepted_estimate_total_value),
                    'label': 'Accepted Estimates'
                },
                'estimate_to_convert': {
                    'count': estimate_to_convert_count,
                    'total_value': float(estimate_to_convert_total_value),
                    'label': 'Estimate to Convert'
                },
                'scheduled_jobs': {
                    'count': scheduled_job_count,
                    'total_value': float(scheduled_job_total_value),
                    'label': 'Scheduled Jobs (Upcoming)'
                },
                'closed_jobs': {
                    'count': closed_job_count,
                    'total_value': float(closed_job_total_value),
                    'label': 'Closed/Completed Jobs'
                }
            },
            'summary_metrics': {
                'pipeline_value': pipeline_value,
                'total_pipeline_items': open_estimate_count + scheduled_job_count,
                'acceptance_rate_percent': round(acceptance_rate, 2),
                'rejection_rate_percent': round(rejection_rate, 2),
                'total_revenue_closed_jobs': float(closed_job_total_value)
            }
        })
    
    @action(detail=False, methods=['get'])
    def sales_forecasting(self, request):
        """
        Sales Forecasting endpoint.
        Returns revenue forecasts for weekly, monthly, quarterly, and yearly periods
        based on historical invoice data.
        """
        user = request.user
        location_id = request.query_params.get('location_id')
        
        # Apply location filter
        location_filter = {}
        if hasattr(user, 'location_id') and user.location_id:
            location_filter['location_id'] = user.location_id
        elif location_id:
            location_filter['location_id'] = location_id
        
        now = timezone.now()
        
        # Get historical invoice data (paid invoices for accurate revenue)
        invoices_qs = Invoice.objects.filter(status='paid')
        if location_filter:
            invoices_qs = invoices_qs.filter(**location_filter)
        
        # Get historical data for the last 2 years for better forecasting
        two_years_ago = now - timedelta(days=730)
        historical_invoices = invoices_qs.filter(
            created_at__gte=two_years_ago,
            created_at__lte=now
        ).exclude(total__isnull=True).exclude(total=0)
        
        # Weekly Forecast
        weekly_data = list(
            historical_invoices.annotate(week=TruncWeek('created_at'))
            .values('week')
            .annotate(total_revenue=Sum('total'), invoice_count=Count('id'))
            .order_by('week')
        )
        
        # Calculate forecast using recent data and trend
        weekly_base = 0
        weekly_trend = 0
        weekly_count_base = 0
        weekly_count_trend = 0
        if weekly_data:
            weekly_totals = [float(item['total_revenue']) for item in weekly_data]
            weekly_counts = [item['invoice_count'] for item in weekly_data]
            
            # Use recent 8 weeks for base average (or all if less than 8)
            recent_weeks = min(8, len(weekly_totals))
            recent_totals = weekly_totals[-recent_weeks:] if len(weekly_totals) >= recent_weeks else weekly_totals
            recent_counts = weekly_counts[-recent_weeks:] if len(weekly_counts) >= recent_weeks else weekly_counts
            weekly_base = sum(recent_totals) / len(recent_totals) if recent_totals else 0
            weekly_count_base = sum(recent_counts) / len(recent_counts) if recent_counts else 0
            
            # Calculate revenue trend (average of last 4 weeks vs previous 4 weeks)
            if len(weekly_totals) >= 8:
                recent_4_avg = sum(weekly_totals[-4:]) / 4
                previous_4_avg = sum(weekly_totals[-8:-4]) / 4
                weekly_trend = (recent_4_avg - previous_4_avg) / 4 if previous_4_avg > 0 else 0
                
                # Calculate invoice count trend
                recent_4_count_avg = sum(weekly_counts[-4:]) / 4
                previous_4_count_avg = sum(weekly_counts[-8:-4]) / 4
                weekly_count_trend = (recent_4_count_avg - previous_4_count_avg) / 4 if previous_4_count_avg > 0 else 0
            elif len(weekly_totals) >= 4:
                # If less than 8 weeks, use last 2 vs previous 2
                recent_2_avg = sum(weekly_totals[-2:]) / 2
                previous_2_avg = sum(weekly_totals[-4:-2]) / 2
                weekly_trend = (recent_2_avg - previous_2_avg) / 2 if previous_2_avg > 0 else 0
                
                recent_2_count_avg = sum(weekly_counts[-2:]) / 2
                previous_2_count_avg = sum(weekly_counts[-4:-2]) / 2
                weekly_count_trend = (recent_2_count_avg - previous_2_count_avg) / 2 if previous_2_count_avg > 0 else 0
        
        # Generate next 8 weeks forecast with trend
        next_8_weeks = []
        for i in range(1, 9):
            week_start = now + timedelta(weeks=i)
            # Apply trend: each week grows/declines by trend amount
            forecasted_revenue = weekly_base + (weekly_trend * i)
            forecasted_revenue = max(0, forecasted_revenue)  # Ensure non-negative
            
            forecasted_count = weekly_count_base + (weekly_count_trend * i)
            forecasted_count = max(0, forecasted_count)  # Ensure non-negative
            
            next_8_weeks.append({
                'week': week_start.isoformat(),
                'week_number': i,
                'forecasted_revenue': round(float(forecasted_revenue), 2),
                'forecasted_invoice_count': round(forecasted_count) if forecasted_count > 0 else 0
            })
        
        # Monthly Forecast
        monthly_data = list(
            historical_invoices.annotate(month=TruncMonth('created_at'))
            .values('month')
            .annotate(total_revenue=Sum('total'), invoice_count=Count('id'))
            .order_by('month')
        )
        
        # Calculate forecast using recent data and trend
        monthly_base = 0
        monthly_trend = 0
        monthly_count_base = 0
        monthly_count_trend = 0
        if monthly_data:
            monthly_totals = [float(item['total_revenue']) for item in monthly_data]
            monthly_counts = [item['invoice_count'] for item in monthly_data]
            
            # Use recent 6 months for base average (or all if less than 6)
            recent_months = min(6, len(monthly_totals))
            recent_totals = monthly_totals[-recent_months:] if len(monthly_totals) >= recent_months else monthly_totals
            recent_counts = monthly_counts[-recent_months:] if len(monthly_counts) >= recent_months else monthly_counts
            monthly_base = sum(recent_totals) / len(recent_totals) if recent_totals else 0
            monthly_count_base = sum(recent_counts) / len(recent_counts) if recent_counts else 0
            
            # Calculate revenue trend (average of last 3 months vs previous 3 months)
            if len(monthly_totals) >= 6:
                recent_3_avg = sum(monthly_totals[-3:]) / 3
                previous_3_avg = sum(monthly_totals[-6:-3]) / 3
                monthly_trend = (recent_3_avg - previous_3_avg) / 3 if previous_3_avg > 0 else 0
                
                # Calculate invoice count trend
                recent_3_count_avg = sum(monthly_counts[-3:]) / 3
                previous_3_count_avg = sum(monthly_counts[-6:-3]) / 3
                monthly_count_trend = (recent_3_count_avg - previous_3_count_avg) / 3 if previous_3_count_avg > 0 else 0
            elif len(monthly_totals) >= 3:
                # If less than 6 months, use last 2 vs previous 1
                recent_2_avg = sum(monthly_totals[-2:]) / 2
                previous_avg = monthly_totals[-3] if len(monthly_totals) >= 3 else monthly_totals[0]
                monthly_trend = (recent_2_avg - previous_avg) / 2 if previous_avg > 0 else 0
                
                recent_2_count_avg = sum(monthly_counts[-2:]) / 2
                previous_count_avg = monthly_counts[-3] if len(monthly_counts) >= 3 else monthly_counts[0]
                monthly_count_trend = (recent_2_count_avg - previous_count_avg) / 2 if previous_count_avg > 0 else 0
        
        # Generate next 12 months forecast with trend
        next_12_months = []
        for i in range(1, 13):
            month = now.month + i
            year = now.year
            while month > 12:
                month -= 12
                year += 1
            month_start = datetime(year, month, 1, tzinfo=now.tzinfo)
            
            # Apply trend: each month grows/declines by trend amount
            forecasted_revenue = monthly_base + (monthly_trend * i)
            forecasted_revenue = max(0, forecasted_revenue)  # Ensure non-negative
            
            forecasted_count = monthly_count_base + (monthly_count_trend * i)
            forecasted_count = max(0, forecasted_count)  # Ensure non-negative
            
            next_12_months.append({
                'month': month_start.isoformat(),
                'month_number': i,
                'forecasted_revenue': round(float(forecasted_revenue), 2),
                'forecasted_invoice_count': round(forecasted_count) if forecasted_count > 0 else 0
            })
        
        # Quarterly Forecast (based on monthly forecast, sum 3 months)
        quarterly_base = monthly_base * 3
        quarterly_trend = monthly_trend * 3
        next_4_quarters = []
        current_quarter = ((now.month - 1) // 3) + 1
        for i in range(1, 5):
            target_quarter = current_quarter + i
            target_year = now.year
            while target_quarter > 4:
                target_quarter -= 4
                target_year += 1
            quarter_start_month = (target_quarter - 1) * 3 + 1
            quarter_start = datetime(target_year, quarter_start_month, 1, tzinfo=now.tzinfo)
            
            # Calculate which months (relative to now) this quarter spans
            # Sum the forecasted revenue and counts for the 3 months in this quarter
            quarter_revenue = 0
            quarter_count = 0
            for month_in_quarter in range(3):
                # Calculate the month index relative to now (1 = next month, 2 = month after, etc.)
                months_from_now = ((target_year - now.year) * 12) + (quarter_start_month + month_in_quarter - now.month)
                if months_from_now > 0:
                    forecasted_revenue = monthly_base + (monthly_trend * months_from_now)
                    forecasted_revenue = max(0, forecasted_revenue)
                    quarter_revenue += forecasted_revenue
                    
                    forecasted_count = monthly_count_base + (monthly_count_trend * months_from_now)
                    forecasted_count = max(0, forecasted_count)
                    quarter_count += forecasted_count
            
            quarter_revenue = max(0, quarter_revenue) if quarter_revenue > 0 else (quarterly_base + (quarterly_trend * i))
            quarter_revenue = max(0, quarter_revenue)
            
            next_4_quarters.append({
                'quarter': quarter_start.isoformat(),
                'quarter_number': i,
                'quarter_label': f'Q{target_quarter} {target_year}',
                'forecasted_revenue': round(float(quarter_revenue), 2),
                'forecasted_invoice_count': round(quarter_count) if quarter_count > 0 else 0
            })
        
        # Yearly Forecast (sum 12 months from monthly forecast)
        yearly_base = monthly_base * 12
        yearly_trend = monthly_trend * 12
        next_2_years = []
        for i in range(1, 3):
            year_start = datetime(now.year + i, 1, 1, tzinfo=now.tzinfo)
            
            # Sum 12 months for the year
            year_revenue = 0
            year_count = 0
            for month_offset in range(1, 13):
                month_idx = ((i - 1) * 12) + month_offset
                forecasted_revenue = monthly_base + (monthly_trend * month_idx)
                forecasted_revenue = max(0, forecasted_revenue)
                year_revenue += forecasted_revenue
                
                forecasted_count = monthly_count_base + (monthly_count_trend * month_idx)
                forecasted_count = max(0, forecasted_count)
                year_count += forecasted_count
            
            year_revenue = max(0, year_revenue)
            
            next_2_years.append({
                'year': year_start.isoformat(),
                'year_number': i,
                'year_label': str(now.year + i),
                'forecasted_revenue': round(float(year_revenue), 2),
                'forecasted_invoice_count': round(year_count) if year_count > 0 else 0
            })
        
        # Historical summary
        total_historical_revenue = historical_invoices.aggregate(Sum('total'))['total__sum'] or 0
        total_historical_invoices = historical_invoices.count()
        
        return Response({
            'forecast_generated_at': now.isoformat(),
            'historical_period': {
                'start_date': two_years_ago.isoformat(),
                'end_date': now.isoformat(),
                'total_revenue': float(total_historical_revenue),
                'total_invoices': total_historical_invoices
            },
            'forecasts': {
                'weekly': {
                    'base_revenue_per_week': round(float(weekly_base), 2),
                    'trend_per_week': round(float(weekly_trend), 2),
                    'periods': next_8_weeks,
                    'total_forecasted_revenue_8_weeks': round(sum([p['forecasted_revenue'] for p in next_8_weeks]), 2)
                },
                'monthly': {
                    'base_revenue_per_month': round(float(monthly_base), 2),
                    'trend_per_month': round(float(monthly_trend), 2),
                    'periods': next_12_months,
                    'total_forecasted_revenue_12_months': round(sum([p['forecasted_revenue'] for p in next_12_months]), 2)
                },
                'quarterly': {
                    'base_revenue_per_quarter': round(float(quarterly_base), 2),
                    'trend_per_quarter': round(float(quarterly_trend), 2),
                    'periods': next_4_quarters,
                    'total_forecasted_revenue_4_quarters': round(sum([p['forecasted_revenue'] for p in next_4_quarters]), 2)
                },
                'yearly': {
                    'base_revenue_per_year': round(float(yearly_base), 2),
                    'trend_per_year': round(float(yearly_trend), 2),
                    'periods': next_2_years,
                    'total_forecasted_revenue_2_years': round(sum([p['forecasted_revenue'] for p in next_2_years]), 2)
                }
            },
            'historical_data_summary': {
                'weekly_trends': weekly_data[-12:] if len(weekly_data) > 12 else weekly_data,  # Last 12 weeks
                'monthly_trends': monthly_data[-12:] if len(monthly_data) > 12 else monthly_data  # Last 12 months
            }
        })


class TechnicianWorkloadHeatmapView(APIView):
    """
    Returns a 7-day (configurable) workload heatmap per technician similar to the
    dashboard mock. The response includes the ordered date headers plus per-technician
    aggregates (job counts, total value, and load intensity classification).
    """

    permission_classes = [IsAuthenticated]
    DEFAULT_STATUSES = [
        status for status, _ in Job.STATUS_CHOICES
        if status not in ('to_convert',)
    ]
    LOAD_THRESHOLDS = (
        (0, 'none'),
        (2, 'light'),     # 1-2 jobs
        (4, 'moderate'),  # 3-4 jobs
        (float('inf'), 'heavy'),  # 5+
    )

    def get(self, request):
        tz = timezone.get_current_timezone()
        start_dt = self._resolve_start_datetime(request.query_params.get('start_date'), tz)
        days = self._resolve_days(request.query_params.get('days'))
        end_dt = start_dt + timedelta(days=days)

        statuses = self._parse_csv(request.query_params.get('statuses')) or self.DEFAULT_STATUSES
        job_types = self._parse_csv(request.query_params.get('job_types'))
        technician_filter = self._parse_id_list(
            request.query_params.get('technicians') or request.query_params.get('technician')
        )
        sort_by = request.query_params.get('sort_by', 'total_value')
        order = request.query_params.get('order', 'desc').lower()
        view_mode = request.query_params.get('view', 'heatmap')

        jobs = Job.objects.filter(
            scheduled_at__isnull=False,
            scheduled_at__gte=start_dt,
            scheduled_at__lt=end_dt,
        ).prefetch_related('assignments__user')

        if statuses:
            jobs = jobs.filter(status__in=statuses)
        if job_types:
            jobs = jobs.filter(job_type__in=job_types)
        if technician_filter:
            jobs = jobs.filter(assignments__user_id__in=technician_filter).distinct()

        date_headers = [
            {
                "date": (start_dt + timedelta(days=i)).date().isoformat(),
                "label": (start_dt + timedelta(days=i)).strftime("%b %d"),
            }
            for i in range(days)
        ]

        technician_map = {}
        available_technicians = {}

        for job in jobs:
            scheduled_local = timezone.localtime(job.scheduled_at, tz)
            date_key = scheduled_local.date().isoformat()
            job_value = float(job.total_price or 0)

            for assignment in job.assignments.all():
                user = assignment.user
                if not user:
                    continue
                if technician_filter and user.id not in technician_filter:
                    continue

                tech_id = str(user.id)
                technician_record = technician_map.setdefault(tech_id, {
                    "technician_id": tech_id,
                    "technician_name": user.get_full_name() or user.username or user.email,
                    "technician_email": user.email,
                    "total_jobs": 0,
                    "total_value": 0.0,
                    "days": {},
                })

                day_bucket = technician_record["days"].setdefault(date_key, {
                    "job_count": 0,
                    "total_value": 0.0,
                })
                day_bucket["job_count"] += 1
                day_bucket["total_value"] += job_value
                technician_record["total_jobs"] += 1
                technician_record["total_value"] += job_value

                if user.id not in available_technicians:
                    available_technicians[user.id] = {
                        "id": tech_id,
                        "name": technician_record["technician_name"],
                    }

        technicians_payload = []
        for record in technician_map.values():
            days_payload = []
            for header in date_headers:
                day_data = record["days"].get(header["date"], {"job_count": 0, "total_value": 0.0})
                load_level = self._determine_load(day_data["job_count"])
                days_payload.append({
                    "date": header["date"],
                    "label": header["label"],
                    "job_count": day_data["job_count"],
                    "total_value": round(day_data["total_value"], 2),
                    "load_level": load_level,
                })

            technicians_payload.append({
                "technician_id": record["technician_id"],
                "technician_name": record["technician_name"],
                "technician_email": record["technician_email"],
                "total_jobs": record["total_jobs"],
                "total_value": round(record["total_value"], 2),
                "days": days_payload,
            })

        reverse = order != 'asc'
        sort_key = {
            'total_jobs': lambda item: item['total_jobs'],
            'name': lambda item: item['technician_name'].lower(),
            'technician_name': lambda item: item['technician_name'].lower(),
            'total_value': lambda item: item['total_value'],
        }.get(sort_by, lambda item: item['total_value'])
        technicians_payload.sort(key=sort_key, reverse=reverse)

        summary = {
            "total_jobs": sum(t["total_jobs"] for t in technicians_payload),
            "total_value": round(sum(t["total_value"] for t in technicians_payload), 2),
        }

        response = {
            "range": {
                "start_date": date_headers[0]["date"] if date_headers else None,
                "end_date": date_headers[-1]["date"] if date_headers else None,
                "days": days,
                "headers": date_headers,
            },
            "filters_applied": {
                "statuses": statuses,
                "job_types": job_types or [],
                "technicians": [str(tid) for tid in technician_filter] if technician_filter else [],
                "sort_by": sort_by,
                "order": order,
                "view": view_mode,
            },
            "legend": [
                {"label": "No jobs", "value": "none"},
                {"label": "Light (1-2)", "value": "light"},
                {"label": "Moderate (3-4)", "value": "moderate"},
                {"label": "Heavy (5+)", "value": "heavy"},
            ],
            "summary": summary,
            "technicians": technicians_payload,
            "available_filters": {
                "job_types": [
                    {"value": value, "label": label}
                    for value, label in Job.JOB_TYPE_CHOICES
                ],
                "statuses": [
                    {"value": value, "label": label}
                    for value, label in Job.STATUS_CHOICES
                ],
                "technicians": list(available_technicians.values()),
                "sort_by": [
                    {"value": "total_value", "label": "Total Amount"},
                    {"value": "total_jobs", "label": "Total Jobs"},
                    {"value": "technician_name", "label": "Technician Name"},
                ],
                "order": [
                    {"value": "asc", "label": "Low to High"},
                    {"value": "desc", "label": "High to Low"},
                ],
            },
        }

        return Response(response)

    @staticmethod
    def _parse_csv(raw_value):
        if not raw_value:
            return []
        return [part.strip() for part in raw_value.split(',') if part.strip()]

    @staticmethod
    def _parse_id_list(raw_value):
        if not raw_value:
            return []
        ids = []
        for part in raw_value.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                ids.append(int(part))
            except ValueError:
                continue
        return ids

    @staticmethod
    def _resolve_days(raw_days):
        try:
            value = int(raw_days)
        except (TypeError, ValueError):
            value = 7
        return min(max(value, 1), 31)

    @staticmethod
    def _resolve_start_datetime(value, tz):
        if value:
            parsed = parse_datetime(value)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, tz)
                local_date = timezone.localtime(parsed, tz).date()
                return timezone.make_aware(datetime.combine(local_date, time.min), tz)
            try:
                date_value = datetime.strptime(value, "%Y-%m-%d").date()
                return timezone.make_aware(datetime.combine(date_value, time.min), tz)
            except ValueError:
                pass

        today_local = timezone.localtime(timezone.now(), tz).date()
        return timezone.make_aware(datetime.combine(today_local, time.min), tz)

    def _determine_load(self, count):
        if count <= 0:
            return 'none'
        if count <= 2:
            return 'light'
        if count <= 4:
            return 'moderate'
        return 'heavy'