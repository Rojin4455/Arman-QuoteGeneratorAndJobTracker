from rest_framework import serializers
from decimal import Decimal
from service_app.models import User
from .models import (
    EmployeeProfile, CollaborationRate, TimeEntry, Payout, PayrollSettings
)


class CollaborationRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CollaborationRate
        fields = ['id', 'member_count', 'percentage', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class CollaborationRateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating collaboration rates"""
    class Meta:
        model = CollaborationRate
        fields = ['id', 'member_count', 'percentage']
        read_only_fields = ['id']
    
    def validate_member_count(self, value):
        """Validate member_count is between 1 and 5"""
        if value < 1 or value > 5:
            raise serializers.ValidationError("member_count must be between 1 and 5")
        return value
    
    def validate_percentage(self, value):
        """Validate percentage is between 0 and 100"""
        if value < 0 or value > 100:
            raise serializers.ValidationError("percentage must be between 0 and 100")
        return value


class EmployeeProfileSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=False
    )
    collaboration_rates = CollaborationRateCreateSerializer(many=True, required=False, write_only=True)
    collaboration_rates_read = serializers.SerializerMethodField(read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    first_name = serializers.CharField(source='user.first_name', read_only=True)
    last_name = serializers.CharField(source='user.last_name', read_only=True)
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = EmployeeProfile
        fields = [
            'id', 'user', 'user_id', 'username', 'email', 'first_name', 'last_name', 'full_name',
            'phone', 'department', 'position', 'timezone',
            'pay_scale_type', 'hourly_rate', 'is_administrator', 'status',
            'collaboration_rates', 'collaboration_rates_read', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username
    
    def get_collaboration_rates_read(self, obj):
        """Read-only field for collaboration rates"""
        rates_qs = getattr(obj.user, 'collaboration_rates', None)
        if hasattr(rates_qs, 'all'):
            queryset = rates_qs.all()
        else:
            queryset = CollaborationRate.objects.filter(employee=obj.user)
        return CollaborationRateSerializer(
            queryset.order_by('member_count'),
            many=True
        ).data
    
    def validate(self, data):
        if not self.instance and not data.get('user'):
            raise serializers.ValidationError({
                'user': 'This field is required.'
            })
        if data.get('pay_scale_type') == 'hourly' and not data.get('hourly_rate'):
            raise serializers.ValidationError({
                'hourly_rate': 'Hourly rate is required for hourly employees'
            })
        return data
    
    def create(self, validated_data):
        """Create employee profile and collaboration rates"""
        collaboration_rates_data = validated_data.pop('collaboration_rates', [])
        employee_profile = EmployeeProfile.objects.create(**validated_data)
        
        # Create collaboration rates if provided
        if collaboration_rates_data:
            for rate_data in collaboration_rates_data:
                CollaborationRate.objects.create(
                    employee=employee_profile.user,
                    member_count=rate_data['member_count'],
                    percentage=rate_data['percentage']
                )
        
        return employee_profile
    
    def update(self, instance, validated_data):
        """Update employee profile and collaboration rates"""
        collaboration_rates_data = validated_data.pop('collaboration_rates', None)
        
        # Update employee profile fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update collaboration rates if provided
        if collaboration_rates_data is not None:
            # Delete existing rates
            CollaborationRate.objects.filter(employee=instance.user).delete()
            
            # Create new rates
            for rate_data in collaboration_rates_data:
                CollaborationRate.objects.create(
                    employee=instance.user,
                    member_count=rate_data['member_count'],
                    percentage=rate_data['percentage']
                )
        
        return instance
    
    def to_representation(self, instance):
        """Override to include collaboration_rates in response"""
        representation = super().to_representation(instance)
        # Use the read-only field for response
        representation['collaboration_rates'] = representation.pop('collaboration_rates_read', [])
        return representation


class CollaborationRateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating collaboration rates"""
    class Meta:
        model = CollaborationRate
        fields = ['id', 'member_count', 'percentage']
        read_only_fields = ['id']
    
    def validate_member_count(self, value):
        """Validate member_count is between 1 and 5"""
        if value < 1 or value > 5:
            raise serializers.ValidationError("member_count must be between 1 and 5")
        return value
    
    def validate_percentage(self, value):
        """Validate percentage is between 0 and 100"""
        if value < 0 or value > 100:
            raise serializers.ValidationError("percentage must be between 0 and 100")
        return value


class TimeEntrySerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_full_name', read_only=True)
    employee_email = serializers.EmailField(source='employee.email', read_only=True)
    
    class Meta:
        model = TimeEntry
        fields = [
            'id', 'employee', 'employee_name', 'employee_email',
            'check_in_time', 'check_out_time', 'total_hours',
            'notes', 'status', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'total_hours', 'status', 'created_at', 'updated_at']
    
    def validate(self, data):
        if 'check_out_time' in data and 'check_in_time' in data:
            if data['check_out_time'] <= data['check_in_time']:
                raise serializers.ValidationError({
                    'check_out_time': 'Check-out time must be after check-in time'
                })
        return data


class PayoutSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.get_full_name', read_only=True)
    employee_email = serializers.EmailField(source='employee.email', read_only=True)
    job_title = serializers.CharField(source='job.title', read_only=True)
    
    class Meta:
        model = Payout
        fields = [
            'id', 'employee', 'employee_name', 'employee_email',
            'payout_type', 'amount', 'time_entry', 'job', 'job_title',
            'project_value', 'rate_percentage', 'project_title', 'notes',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class PayrollSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollSettings
        fields = [
            'id', 'first_time_bonus_percentage', 'quoted_by_bonus_percentage', 'updated_at'
        ]
        read_only_fields = ['id', 'updated_at']

