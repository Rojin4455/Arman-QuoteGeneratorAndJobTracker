from rest_framework import serializers
from .models import Job, JTService, JobServiceItem, JobAssignment, JobOccurrence
from datetime import datetime, timedelta
import calendar


class JTServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = JTService
        fields = ['id', 'name', 'description', 'default_duration_hours', 'default_price', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


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


class JobSerializer(serializers.ModelSerializer):
    items = JobServiceItemSerializer(many=True, required=False)
    assignments = JobAssignmentSerializer(many=True, required=False)
    occurrence_count = serializers.IntegerField(source='occurrences', read_only=True)
    occurrence_events = JobOccurrenceSerializer(many=True, read_only=True, source='schedule_occurrences')

    class Meta:
        model = Job
        fields = [
            'id', 'submission', 'title', 'description', 'priority', 'duration_hours', 'scheduled_at',
            'total_price',
            'customer_name', 'customer_phone', 'customer_email', 'customer_address', 'ghl_contact_id',
            'quoted_by', 'created_by', 'created_by_email',
            'job_type', 'repeat_every', 'repeat_unit', 'occurrences',
            'status', 'notes', 'items', 'assignments',
            'occurrence_count', 'occurrence_events',
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
        scheduling_fields = ['job_type', 'repeat_every', 'repeat_unit', 'occurrences', 'scheduled_at']
        if any(f in self.initial_data for f in scheduling_fields):
            self._rebuild_occurrences(instance)

        return instance

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
        dates = self._build_occurrence_datetimes(job.scheduled_at, job.repeat_every, job.repeat_unit, job.occurrences)
        for idx, dt in enumerate(dates, start=1):
            JobOccurrence.objects.create(job=job, scheduled_at=dt, sequence=idx)

    def _build_occurrence_datetimes(self, start_dt, repeat_every, repeat_unit, occurrences):
        result = []
        current = start_dt
        for i in range(occurrences):
            if i == 0:
                result.append(current)
                continue
            if repeat_unit == 'day':
                current = current + timedelta(days=repeat_every)
            elif repeat_unit == 'week':
                current = current + timedelta(weeks=repeat_every)
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


