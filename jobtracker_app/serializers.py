from rest_framework import serializers
from .models import Job, JobServiceItem, JobAssignment, JobOccurrence
from datetime import datetime, timedelta
import calendar
from service_app.models import User, Service


class JobServiceItemSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = JobServiceItem
        fields = ['id', 'service', 'service_name', 'custom_name', 'price', 'duration_hours']
        read_only_fields = ['id']


class JobAssignmentSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = JobAssignment
        fields = ['id', 'user', 'user_email', 'user_name', 'role']
        read_only_fields = ['id']


class JobOccurrenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobOccurrence
        fields = ['id', 'scheduled_at', 'sequence']
        read_only_fields = ['id']


class OccurrenceEventSerializer(serializers.ModelSerializer):
    job_id = serializers.UUIDField(source='job.id')
    title = serializers.CharField(source='job.title')
    status = serializers.CharField(source='job.status')
    priority = serializers.CharField(source='job.priority')
    duration_hours = serializers.DecimalField(source='job.duration_hours', max_digits=5, decimal_places=2)

    class Meta:
        model = JobOccurrence
        fields = [
            'id', 'job_id', 'title', 'scheduled_at', 'sequence',
            'status', 'priority', 'duration_hours'
        ]


class CalendarEventSerializer(serializers.ModelSerializer):
    """Serializer for calendar view - works with Job model directly (supports both one-time and recurring series instances)"""
    job_id = serializers.UUIDField(source='id')

    class Meta:
        model = Job
        fields = [
            'job_id', 'title', 'scheduled_at', 'status', 'priority',
            'duration_hours', 'total_price', 'customer_name',
            'series_id', 'series_sequence'
        ]


class JobSerializer(serializers.ModelSerializer):
    items = JobServiceItemSerializer(many=True, required=False)
    assignments = JobAssignmentSerializer(many=True, required=False)
    occurrence_count = serializers.IntegerField(source='occurrences', read_only=True)
    occurrence_events = JobOccurrenceSerializer(many=True, read_only=True, source='schedule_occurrences')
    series_id = serializers.UUIDField(read_only=True)
    series_sequence = serializers.IntegerField(read_only=True)

    class Meta:
        model = Job
        fields = [
            'id', 'submission', 'title', 'description', 'priority', 'duration_hours', 'scheduled_at',
            'total_price',
            'customer_name', 'customer_phone', 'customer_email', 'customer_address', 'ghl_contact_id',
            'quoted_by', 'created_by', 'created_by_email',
            'job_type', 'repeat_every', 'repeat_unit', 'occurrences', 'day_of_week',
            'status', 'notes', 'items', 'assignments',
            'occurrence_count', 'occurrence_events', 'series_id', 'series_sequence',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        assignments_data = validated_data.pop('assignments', [])
        job = Job.objects.create(**validated_data)

        for item in items_data:
            JobServiceItem.objects.create(job=job, **item)
        for assign in assignments_data:
            JobAssignment.objects.create(job=job, **assign)
        self._rebuild_occurrences(job)
        return job

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        assignments_data = validated_data.pop('assignments', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item in items_data:
                JobServiceItem.objects.create(job=instance, **item)

        if assignments_data is not None:
            instance.assignments.all().delete()
            for assign in assignments_data:
                JobAssignment.objects.create(job=instance, **assign)

        # Rebuild occurrences if any scheduling fields changed
        scheduling_fields = ['job_type', 'repeat_every', 'repeat_unit', 'occurrences', 'day_of_week', 'scheduled_at']
        if any(f in self.initial_data for f in scheduling_fields):
            self._rebuild_occurrences(instance)

        return instance

    def validate(self, data):
        repeat_unit = data.get('repeat_unit')
        day_of_week = data.get('day_of_week')
        
        # If repeat_unit is 'week', day_of_week should be provided
        if repeat_unit == 'week' and day_of_week is None:
            raise serializers.ValidationError({
                'day_of_week': 'day_of_week is required when repeat_unit is "week"'
            })
        
        # If repeat_unit is not 'week', day_of_week should be None
        if repeat_unit and repeat_unit != 'week' and day_of_week is not None:
            raise serializers.ValidationError({
                'day_of_week': 'day_of_week should only be provided when repeat_unit is "week"'
            })
        
        return data

    # ===== recurrence helpers =====
    def _rebuild_occurrences(self, job: Job):
        JobOccurrence.objects.filter(job=job).delete()
        if not job.scheduled_at:
            return
        if job.job_type == 'one_time':
            JobOccurrence.objects.create(job=job, scheduled_at=job.scheduled_at, sequence=1)
            return
        if job.job_type != 'recurring':
            return
        if not job.repeat_every or not job.repeat_unit or not job.occurrences:
            return
        dates = self._build_occurrence_datetimes(
            job.scheduled_at, 
            job.repeat_every, 
            job.repeat_unit, 
            job.occurrences,
            day_of_week=job.day_of_week
        )
        for idx, dt in enumerate(dates, start=1):
            JobOccurrence.objects.create(job=job, scheduled_at=dt, sequence=idx)

    def _build_occurrence_datetimes(self, start_dt, repeat_every, repeat_unit, occurrences, day_of_week=None):
        result = []
        current = start_dt
        
        for i in range(occurrences):
            if i == 0:
                # For the first occurrence, if it's weekly and day_of_week is specified,
                # adjust to the correct day of week
                if repeat_unit == 'week' and day_of_week is not None:
                    # Get the current weekday (Python's weekday() is 0=Monday, 6=Sunday)
                    current_weekday = current.weekday()  # 0=Monday, 6=Sunday
                    days_to_add = (day_of_week - current_weekday) % 7
                    if days_to_add > 0:
                        current = current + timedelta(days=days_to_add)
                result.append(current)
                continue
                
            if repeat_unit == 'day':
                current = current + timedelta(days=repeat_every)
            elif repeat_unit == 'week':
                # For weekly, add the number of weeks
                current = current + timedelta(weeks=repeat_every)
                # Ensure it's on the correct day of week
                if day_of_week is not None:
                    current_weekday = current.weekday()
                    days_to_add = (day_of_week - current_weekday) % 7
                    if days_to_add > 0:
                        current = current + timedelta(days=days_to_add)
            elif repeat_unit in ['month', 'quarter', 'semi_annual', 'year']:
                months_to_add = repeat_every
                if repeat_unit == 'quarter':
                    months_to_add = 3 * repeat_every
                elif repeat_unit == 'semi_annual':
                    months_to_add = 6 * repeat_every
                elif repeat_unit == 'year':
                    months_to_add = 12 * repeat_every
                current = self._add_months(current, months_to_add)
            else:
                current = current + timedelta(days=repeat_every)
            result.append(current)
        return result

    def _add_months(self, dt, months):
        month = dt.month - 1 + months
        year = dt.year + month // 12
        month = month % 12 + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)


class JobSeriesCreateSerializer(serializers.Serializer):
    # base job fields
    title = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    priority = serializers.ChoiceField(choices=['low', 'medium', 'high'], default='low')
    duration_hours = serializers.DecimalField(max_digits=5, decimal_places=2)
    scheduled_at = serializers.DateTimeField()
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    customer_name = serializers.CharField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_address = serializers.CharField(required=False, allow_blank=True)
    ghl_contact_id = serializers.CharField(required=False, allow_blank=True)
    # Accept either UUID string or omit. We'll map to quoted_by_id in create
    quoted_by = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    # recurrence
    repeat_every = serializers.IntegerField(min_value=1)
    repeat_unit = serializers.ChoiceField(choices=['day', 'week', 'month', 'quarter', 'semi_annual', 'year'])
    occurrences = serializers.IntegerField(min_value=1)
    day_of_week = serializers.IntegerField(min_value=0, max_value=6, required=False, allow_null=True)
    # nested
    items = JobServiceItemSerializer(many=True, required=False)
    assignments = JobAssignmentSerializer(many=True, required=False)

    def create(self, validated):
        from uuid import uuid4
        base_dt = validated.pop('scheduled_at')
        repeat_every = validated.pop('repeat_every')
        repeat_unit = validated.pop('repeat_unit')
        count = validated.pop('occurrences')
        day_of_week = validated.pop('day_of_week', None)
        items = validated.pop('items', [])
        assigns = validated.pop('assignments', [])
        quoted_by_raw = validated.pop('quoted_by', None)

        request = self.context.get('request')
        creator = request.user if request and request.user.is_authenticated else None

        # build dates using the existing helper, passing day_of_week
        dates = JobSerializer._build_occurrence_datetimes(
            self, base_dt, repeat_every, repeat_unit, count, day_of_week=day_of_week
        )
        series = uuid4()
        created_ids = []

        for idx, dt in enumerate(dates, start=1):
            # normalize quoted_by: accept uuid/email/username
            qb_id = None
            if quoted_by_raw:
                qb_id = self._resolve_user_id(quoted_by_raw)
            job = Job.objects.create(
                **validated,
                scheduled_at=dt,
                job_type='recurring',
                repeat_every=repeat_every,
                repeat_unit=repeat_unit,
                occurrences=count,
                day_of_week=day_of_week,
                status='scheduled',
                created_by=creator,
                created_by_email=getattr(creator, 'email', None),
                series_id=series,
                series_sequence=idx,
                **({ 'quoted_by_id': qb_id } if qb_id else {})
            )
            for it in items:
                # Accept either service UUID or a service name, or a pure custom item
                service_ref = it.get('service')
                service_id = None
                if service_ref:
                    ref_str = str(service_ref)
                    # naive UUID check
                    if len(ref_str) == 36 and ref_str.count('-') == 4:
                        service_id = ref_str
                    else:
                        svc = Service.objects.filter(name=ref_str).first()
                        if svc:
                            service_id = str(svc.id)

                JobServiceItem.objects.create(
                    job=job,
                    service_id=service_id,
                    custom_name=it.get('custom_name'),
                    price=it.get('price', '0'),
                    duration_hours=it.get('duration_hours', '0'),
                )
            for a in assigns:
                user_ref = a.get('user')
                user_id = self._resolve_user_id(user_ref) if user_ref is not None else None
                JobAssignment.objects.create(
                    job=job,
                    user_id=user_id,
                    role=a.get('role')
                )
            created_ids.append(str(job.id))

        return {'series_id': str(series), 'job_ids': created_ids}

    def validate(self, data):
        repeat_unit = data.get('repeat_unit')
        day_of_week = data.get('day_of_week')
        
        # If repeat_unit is 'week', day_of_week should be provided
        if repeat_unit == 'week' and day_of_week is None:
            raise serializers.ValidationError({
                'day_of_week': 'day_of_week is required when repeat_unit is "week"'
            })
        
        # If repeat_unit is not 'week', day_of_week should be None
        if repeat_unit and repeat_unit != 'week' and day_of_week is not None:
            raise serializers.ValidationError({
                'day_of_week': 'day_of_week should only be provided when repeat_unit is "week"'
            })
        
        return data

    def _resolve_user_id(self, ref):
        if ref is None:
            return None
        ref_str = str(ref).strip()
        # UUID-like
        if len(ref_str) == 36 and ref_str.count('-') == 4:
            return ref_str
        # Email
        if '@' in ref_str:
            u = User.objects.filter(email=ref_str).only('id').first()
            return str(u.id) if u else None
        # Username
        u = User.objects.filter(username=ref_str).only('id').first()
        return str(u.id) if u else None


class LocationSummarySerializer(serializers.Serializer):
    """Serializer for location summary card data"""
    address = serializers.CharField()
    job_count = serializers.IntegerField()
    customer_names = serializers.ListField(child=serializers.CharField())
    status_counts = serializers.DictField()
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_hours = serializers.DecimalField(max_digits=8, decimal_places=2)
    next_scheduled = serializers.DateTimeField(allow_null=True)
    service_names = serializers.ListField(child=serializers.CharField())
    job_ids = serializers.ListField(child=serializers.UUIDField())