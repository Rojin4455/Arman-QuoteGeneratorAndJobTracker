# serializers.py
from rest_framework import serializers
from .models import (
    Service, Package, 
    Question, QuestionOption, Quote, QuoteQuestionAnswer,
    Location
)
from accounts.models import Contact
from service_app.models import Feature, Package, PackageFeature, OptionPricing, QuestionPricing
from decimal import Decimal
from geopy.distance import geodesic


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ['id', 'first_name', 'phone_number', 'email', 'address', 
                 'latitude', 'longitude', 'google_place_id', 'created_at']
        read_only_fields = ['id', 'created_at']


class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ['id', 'name', 'description']


class PackageFeatureSerializer(serializers.ModelSerializer):
    feature = FeatureSerializer(read_only=True)
    
    class Meta:
        model = PackageFeature
        fields = ['feature', 'is_included']


class PackageSerializer(serializers.ModelSerializer):
    features = serializers.SerializerMethodField()
    
    class Meta:
        model = Package
        fields = ['id', 'name', 'base_price', 'order', 'features']
    
    def get_features(self, obj):
        package_features = obj.package_features.filter(feature__is_active=True)
        return PackageFeatureSerializer(package_features, many=True).data


class ServiceSerializer(serializers.ModelSerializer):
    packages = PackageSerializer(many=True, read_only=True)
    
    class Meta:
        model = Service
        fields = ['id', 'name', 'description', 'order', 'packages']


class ServiceListSerializer(serializers.ModelSerializer):
    """Simplified serializer for listing services without packages"""
    class Meta:
        model = Service
        fields = ['id', 'name', 'description', 'order']


class QuestionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ['id', 'option_text', 'order']


class QuestionOptionWithPricingSerializer(serializers.ModelSerializer):
    pricing_type = serializers.CharField(read_only=True)
    value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    class Meta:
        model = QuestionOption
        fields = ['id', 'option_text', 'order', 'pricing_type', 'value']


class QuestionSerializer(serializers.ModelSerializer):
    options = QuestionOptionSerializer(many=True, read_only=True)
    
    class Meta:
        model = Question
        fields = ['id', 'question_text', 'question_type', 'order', 'options']


class QuestionWithPricingSerializer(serializers.ModelSerializer):
    # For yes/no questions
    yes_pricing_type = serializers.CharField(read_only=True)
    yes_value = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    # For options questions - will be populated dynamically
    options = QuestionOptionWithPricingSerializer(many=True, read_only=True)
    
    class Meta:
        model = Question
        fields = ['id', 'question_text', 'question_type', 'order', 'yes_pricing_type', 'yes_value', 'options']


class QuoteQuestionAnswerSerializer(serializers.ModelSerializer):
    question = QuestionSerializer(read_only=True)
    question_id = serializers.UUIDField(write_only=True)
    selected_option_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = QuoteQuestionAnswer
        fields = ['id', 'question', 'question_id', 'yes_no_answer', 
                 'selected_option', 'selected_option_id', 'price_adjustment']
        read_only_fields = ['id', 'price_adjustment']


class QuoteSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)
    service = ServiceListSerializer(read_only=True)
    package = PackageSerializer(read_only=True)
    question_answers = QuoteQuestionAnswerSerializer(many=True, read_only=True)
    nearest_location_name = serializers.CharField(source='nearest_location.name', read_only=True)
    
    class Meta:
        model = Quote
        fields = ['id', 'contact', 'service', 'package', 'nearest_location_name',
                 'distance_to_location', 'base_price', 'trip_surcharge', 
                 'question_adjustments', 'total_price', 'status', 
                 'question_answers', 'created_at']
        read_only_fields = ['id', 'base_price', 'trip_surcharge', 'question_adjustments', 
                           'total_price', 'status', 'created_at']


class QuoteCreateSerializer(serializers.Serializer):
    contact_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    package_id = serializers.UUIDField()
    answers = serializers.ListField(
        child=serializers.DictField(), 
        required=False, 
        allow_empty=True
    )
    
    def validate(self, data):
        # Validate contact exists
        try:
            contact = Contact.objects.get(id=data['contact_id'])
        except Contact.DoesNotExist:
            raise serializers.ValidationError("Contact not found")
        
        # Validate service exists and is active
        try:
            service = Service.objects.get(id=data['service_id'], is_active=True)
        except Service.DoesNotExist:
            raise serializers.ValidationError("Service not found or inactive")
        
        # Validate package exists, is active, and belongs to service
        try:
            package = Package.objects.get(
                id=data['package_id'], 
                service=service, 
                is_active=True
            )
        except Package.DoesNotExist:
            raise serializers.ValidationError("Package not found or doesn't belong to service")
        
        # Validate answers format if provided
        if 'answers' in data:
            for answer in data['answers']:
                if 'question_id' not in answer:
                    raise serializers.ValidationError("Each answer must have question_id")
                
                # Check if question belongs to service
                try:
                    question = Question.objects.get(
                        id=answer['question_id'], 
                        service=service, 
                        is_active=True
                    )
                except Question.DoesNotExist:
                    raise serializers.ValidationError(f"Question {answer['question_id']} not found or doesn't belong to service")
                
                # Validate answer format based on question type
                if question.question_type == 'yes_no':
                    if 'yes_no_answer' not in answer:
                        raise serializers.ValidationError(f"Question {question.id} requires yes_no_answer")
                elif question.question_type == 'options':
                    if 'selected_option_id' not in answer:
                        raise serializers.ValidationError(f"Question {question.id} requires selected_option_id")
                    
                    # Validate option belongs to question
                    try:
                        QuestionOption.objects.get(
                            id=answer['selected_option_id'],
                            question=question,
                            is_active=True
                        )
                    except QuestionOption.DoesNotExist:
                        raise serializers.ValidationError(f"Option {answer['selected_option_id']} not found or doesn't belong to question")
        
        data['contact'] = contact
        data['service'] = service
        data['package'] = package
        
        return data
    

