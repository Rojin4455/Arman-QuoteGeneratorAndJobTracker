# views.py
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from django.db.models import Count, Avg, Prefetch
from django.db import transaction
from django.shortcuts import get_object_or_404
from decimal import Decimal
import requests
import os
from django.db import models
from .models import Service, ServiceSettings
from .serializers import ServiceSettingsSerializer

from rest_framework.permissions import IsAuthenticated

from .models import (
    User, Location, Service, Package, Feature, PackageFeature,
    Question, QuestionOption, QuestionPricing, OptionPricing,
    Order, OrderQuestionAnswer,SubQuestionPricing,SubQuestion,QuestionResponse, GlobalBasePrice
)
from .serializers import (
    UserSerializer, LoginSerializer, LocationSerializer, ServiceSerializer,
    ServiceListSerializer, ServiceBasicSerializer, PackageSerializer, FeatureSerializer,
    PackageFeatureSerializer, QuestionSerializer, QuestionCreateSerializer,
    QuestionOptionSerializer, QuestionPricingSerializer, OptionPricingSerializer,
    PackageWithFeaturesSerializer, BulkPricingUpdateSerializer,
    ServiceAnalyticsSerializer, SubQuestionPricingSerializer,BulkSubQuestionPricingSerializer,QuestionResponseSerializer,
    PricingCalculationSerializer, SubQuestionSerializer,GlobalBasePriceSerializer
)



from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class IsAdminPermission(permissions.BasePermission):
    """Custom permission to only allow admins to access views"""
    
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.is_admin
    


class AdminTokenObtainPairView(TokenObtainPairView):
    permission_classes = [AllowAny]
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the user from validated data
        user = serializer.user
        
        # Get tokens
        refresh = RefreshToken.for_user(user)
        access_token = refresh.access_token
        
        # Serialize user data
        user_data = UserSerializer(user).data
        
        # Check if user has employee profile
        employee_profile = None
        try:
            from payroll_app.models import EmployeeProfile
            from payroll_app.serializers import EmployeeProfileSerializer
            profile = user.employee_profile
            employee_profile = EmployeeProfileSerializer(profile).data
        except (AttributeError, ImportError):
            pass
        except Exception:
            # Handle DoesNotExist or other exceptions
            pass
        
        # Build response with tokens and user data
        response_data = {
            'access': str(access_token),
            'refresh': str(refresh),
            'user': user_data
        }
        
        if employee_profile:
            response_data['employee_profile'] = employee_profile
        
        return Response(response_data, status=status.HTTP_200_OK)

class AdminTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

# Public user JWT login/refresh (no admin restriction)
class UserTokenObtainPairView(TokenObtainPairView):
    permission_classes = [AllowAny]
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the user from validated data
        user = serializer.user
        
        # Get tokens
        refresh = RefreshToken.for_user(user)
        access_token = refresh.access_token
        
        # Serialize user data
        user_data = UserSerializer(user).data
        
        # Check if user has employee profile
        employee_profile = None
        try:
            from payroll_app.models import EmployeeProfile
            from payroll_app.serializers import EmployeeProfileSerializer
            profile = user.employee_profile
            employee_profile = EmployeeProfileSerializer(profile).data
        except (AttributeError, ImportError):
            pass
        except Exception:
            # Handle DoesNotExist or other exceptions
            pass
        
        # Build response with tokens and user data
        response_data = {
            'access': str(access_token),
            'refresh': str(refresh),
            'user': user_data
        }
        
        if employee_profile:
            response_data['employee_profile'] = employee_profile
        
        return Response(response_data, status=status.HTTP_200_OK)

class UserTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

class AdminLogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()  # Requires Blacklist app enabled
            return Response({"detail": "Successfully logged out."})
        except Exception as e:
            return Response({"detail": "Invalid token or already logged out."}, status=400)



# Authentication Views
class AdminLoginView(APIView):
    """Admin login view"""
    permission_classes = []

    def post(self, request):
        print("request: ", request.data)
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data['user']
            token, created = Token.objects.get_or_create(user=user)
            return Response({
                'token': token.key,
                'user': UserSerializer(user).data,
                'message': 'Login successful'
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class AdminLogoutView(APIView):
#     """Admin logout view"""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         try:
#             request.user.auth_token.delete()
#             return Response({'message': 'Logout successful'})
#         except:
#             return Response({'message': 'Logout successful'})


# Location Views
class LocationListCreateView(generics.ListCreateAPIView):
    """List all locations and create new ones"""
    queryset = Location.objects.filter(is_active=True)
    serializer_class = LocationSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search) | 
                models.Q(address__icontains=search)
            )
        return queryset.order_by('name')


class LocationDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a location"""
    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    permission_classes = [IsAdminPermission]

    def perform_destroy(self, instance):
        # Soft delete
        instance.is_active = False
        instance.save()





# Service Views
class ServiceListCreateView(generics.ListCreateAPIView):
    """List all services and create new ones"""
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Service.objects.all().prefetch_related(
            'questions__options',
            'questions__sub_questions',
            'questions__child_questions'
        )
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset.order_by('order', 'name')

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return ServiceListSerializer
        return ServiceSerializer

class ServiceBasicListView(generics.ListAPIView):
    """Simple endpoint to list all services with basic details only"""
    queryset = Service.objects.filter(is_active=True).order_by('order', 'name')
    serializer_class = ServiceBasicSerializer
    permission_classes = [AllowAny]  # Public endpoint, can be changed to IsAuthenticated if needed
    
    def get_queryset(self):
        queryset = super().get_queryset()
        # Optional: filter by is_active if not already filtered
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            is_active_bool = is_active.lower() == 'true'
            queryset = queryset.filter(is_active=is_active_bool)
        return queryset

class ServiceDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a service"""
    queryset = Service.objects.prefetch_related(
        'questions__options__pricing_rules',
        'questions__sub_questions__pricing_rules',
        'questions__pricing_rules',
        'questions__child_questions'
    )
    serializer_class = ServiceSerializer
    permission_classes = [IsAdminPermission]

    # def perform_destroy(self, instance):
    #     # Soft delete
    #     instance.delete
    #     instance.is_active = False
    #     instance.save()



# Package Views
class PackageListCreateView(generics.ListCreateAPIView):
    """List all packages and create new ones"""
    queryset = Package.objects.filter(is_active=True).select_related('service')
    serializer_class = PackageSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        service_id = self.request.query_params.get('service', None)
        if service_id:
            queryset = queryset.filter(service_id=service_id)
        return queryset.order_by('service__name', 'order', 'name')


class PackageDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a package"""
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsAdminPermission]

    # def perform_destroy(self, instance):
    #     # Soft delete
    #     # instance.is_active = True
    #     instance.delete()


class PackageWithFeaturesView(generics.RetrieveAPIView):
    """Get package with its features"""
    queryset = Package.objects.prefetch_related('package_features__feature')
    serializer_class = PackageWithFeaturesSerializer
    permission_classes = [IsAdminPermission]


# Feature Views
class FeatureListCreateView(generics.ListCreateAPIView):
    """List all features and create new ones"""
    queryset = Feature.objects.filter(is_active=True).select_related('service')
    serializer_class = FeatureSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        service_id = self.request.query_params.get('service', None)
        if service_id:
            queryset = queryset.filter(service_id=service_id)
        return queryset.order_by('service__name', 'name')


class FeatureDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a feature"""
    queryset = Feature.objects.all()
    serializer_class = FeatureSerializer
    permission_classes = [IsAdminPermission]

    # def perform_destroy(self, instance):
    #     # Soft delete
    #     instance.is_active = False
    #     instance.save()


# Package-Feature Views
class PackageFeatureListCreateView(generics.ListCreateAPIView):
    """List and create package-feature relationships"""
    queryset = PackageFeature.objects.select_related('package', 'feature')
    serializer_class = PackageFeatureSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        package_id = self.request.query_params.get('package', None)
        if package_id:
            queryset = queryset.filter(package_id=package_id)
        return queryset


class PackageFeatureDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a package-feature relationship"""
    queryset = PackageFeature.objects.all()
    serializer_class = PackageFeatureSerializer
    permission_classes = [IsAdminPermission]


# Question Views
class QuestionListCreateView(generics.ListCreateAPIView):
    """List all questions and create new ones"""
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = Question.objects.filter(is_active=True).select_related(
            'service', 'parent_question', 'condition_option'
        ).prefetch_related(
            'options__pricing_rules',
            'sub_questions__pricing_rules',
            'pricing_rules__package',
            'child_questions'
        )
        
        # Filter parameters
        service_id = self.request.query_params.get('service', None)
        question_type = self.request.query_params.get('type', None)
        parent_only = self.request.query_params.get('parent_only', 'false').lower() == 'true'
        
        if service_id:
            queryset = queryset.filter(service_id=service_id)
        if question_type:
            queryset = queryset.filter(question_type=question_type)
        if parent_only:
            queryset = queryset.filter(parent_question__isnull=True)
            
        return queryset.order_by('service__name', 'order', 'created_at')

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return QuestionCreateSerializer
        return QuestionSerializer

class QuestionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a question"""
    queryset = Question.objects.prefetch_related(
        'options__pricing_rules',
        'sub_questions__pricing_rules',
        'pricing_rules',
        'child_questions'
    )
    permission_classes = [IsAdminPermission]

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return QuestionCreateSerializer
        return QuestionSerializer

    def perform_destroy(self, instance):
        # Soft delete
        instance.is_active = False
        instance.save()

# Question Option Views
class QuestionOptionListCreateView(generics.ListCreateAPIView):
    """List and create question options"""
    queryset = QuestionOption.objects.filter(is_active=True)
    serializer_class = QuestionOptionSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related('pricing_rules')
        question_id = self.request.query_params.get('question', None)
        if question_id:
            queryset = queryset.filter(question_id=question_id)
        return queryset.order_by('order', 'option_text')


class QuestionOptionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a question option"""
    queryset = QuestionOption.objects.prefetch_related('pricing_rules')
    serializer_class = QuestionOptionSerializer
    permission_classes = [IsAdminPermission]

    # def perform_destroy(self, instance):
    #     # Soft delete
    #     instance.is_active = False
    #     instance.save()

# Pricing Views
class QuestionPricingListCreateView(generics.ListCreateAPIView):
    """List and create question pricing rules"""
    queryset = QuestionPricing.objects.select_related('question', 'package')
    serializer_class = QuestionPricingSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        question_id = self.request.query_params.get('question', None)
        package_id = self.request.query_params.get('package', None)
        
        if question_id:
            queryset = queryset.filter(question_id=question_id)
        if package_id:
            queryset = queryset.filter(package_id=package_id)
            
        return queryset


class QuestionPricingDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a question pricing rule"""
    queryset = QuestionPricing.objects.all()
    serializer_class = QuestionPricingSerializer
    permission_classes = [IsAdminPermission]



class SubQuestionPricingListCreateView(generics.ListCreateAPIView):
    """List and create sub-question pricing rules"""
    queryset = SubQuestionPricing.objects.select_related('sub_question', 'package')
    serializer_class = SubQuestionPricingSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        sub_question_id = self.request.query_params.get('sub_question', None)
        package_id = self.request.query_params.get('package', None)
        
        if sub_question_id:
            queryset = queryset.filter(sub_question_id=sub_question_id)
        if package_id:
            queryset = queryset.filter(package_id=package_id)
            
        return queryset


class SubQuestionPricingDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a sub-question pricing rule"""
    queryset = SubQuestionPricing.objects.all()
    serializer_class = SubQuestionPricingSerializer
    permission_classes = [IsAdminPermission]


class SubQuestionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete a sub-question"""
    queryset = SubQuestion.objects.prefetch_related('pricing_rules')
    serializer_class = SubQuestionSerializer
    permission_classes = [IsAdminPermission]

    def perform_destroy(self, instance):
        # Soft delete
        instance.is_active = False
        instance.save()


class OptionPricingListCreateView(generics.ListCreateAPIView):
    """List and create option pricing rules"""
    queryset = OptionPricing.objects.select_related('option', 'package')
    serializer_class = OptionPricingSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        option_id = self.request.query_params.get('option', None)
        package_id = self.request.query_params.get('package', None)
        
        if option_id:
            queryset = queryset.filter(option_id=option_id)
        if package_id:
            queryset = queryset.filter(package_id=package_id)
            
        return queryset



class OptionPricingDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update or delete an option pricing rule"""
    queryset = OptionPricing.objects.all()
    serializer_class = OptionPricingSerializer
    permission_classes = [IsAdminPermission]



class SubQuestionListCreateView(generics.ListCreateAPIView):
    """List and create sub-questions"""
    queryset = SubQuestion.objects.filter(is_active=True)
    serializer_class = SubQuestionSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related('pricing_rules')
        parent_question_id = self.request.query_params.get('parent_question', None)
        if parent_question_id:
            queryset = queryset.filter(parent_question_id=parent_question_id)
        return queryset.order_by('order', 'sub_question_text')


# Bulk Operations Views
class BulkQuestionPricingView(APIView):
    """Bulk update question pricing rules for all packages"""
    permission_classes = [IsAdminPermission]

    def post(self, request):
        serializer = BulkPricingUpdateSerializer(data=request.data)
        if serializer.is_valid():
            question_id = serializer.validated_data['question_id']
            pricing_rules = serializer.validated_data['pricing_rules']

            try:
                with transaction.atomic():
                    question = get_object_or_404(Question, id=question_id)
                    
                    # Update or create pricing rules
                    for rule in pricing_rules:
                        package_id = rule['package_id']
                        pricing_type = rule['pricing_type']
                        value = Decimal(str(rule['value']))
                        
                        # package = get_object_or_404(Package, id=package_id)
                        
                        pricing, created = QuestionPricing.objects.get_or_create(
                            question=question,
                            package_id=package_id,
                            defaults={
                                'yes_pricing_type': pricing_type,
                                'yes_value': value
                            }
                        )
                        
                        if not created:
                            pricing.yes_pricing_type = pricing_type
                            pricing.yes_value = value
                            pricing.save()

                return Response({'message': 'Question pricing rules updated successfully'})
                
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class BulkSubQuestionPricingView(APIView):
    """Bulk update sub-question pricing rules for all packages"""
    permission_classes = [IsAdminPermission]

    def post(self, request):
        serializer = BulkSubQuestionPricingSerializer(data=request.data)
        if serializer.is_valid():
            sub_question_id = serializer.validated_data['sub_question_id']
            pricing_rules = serializer.validated_data['pricing_rules']

            try:
                with transaction.atomic():
                    sub_question = get_object_or_404(SubQuestion, id=sub_question_id)
                    
                    for rule in pricing_rules:
                        package_id = rule['package_id']
                        pricing_type = rule['pricing_type']
                        value = Decimal(str(rule['value']))
                        
                        pricing, created = SubQuestionPricing.objects.get_or_create(
                            sub_question=sub_question,
                            package_id=package_id,
                            defaults={
                                'yes_pricing_type': pricing_type,
                                'yes_value': value
                            }
                        )
                        
                        if not created:
                            pricing.yes_pricing_type = pricing_type
                            pricing.yes_value = value
                            pricing.save()

                return Response({'message': 'Sub-question pricing rules updated successfully'})
                
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BulkOptionPricingView(APIView):
    """Bulk update option pricing rules for all packages"""
    permission_classes = [IsAdminPermission]

    def post(self, request):
        option_id = request.data.get('option_id')
        pricing_rules = request.data.get('pricing_rules', [])

        if not option_id or not pricing_rules:
            return Response({'error': 'option_id and pricing_rules are required'}, 
                          status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                option = get_object_or_404(QuestionOption, id=option_id)
                
                for rule in pricing_rules:
                    package_id = rule['package_id']
                    pricing_type = rule['pricing_type']
                    value = Decimal(str(rule['value']))
                    
                    pricing, created = OptionPricing.objects.get_or_create(
                        option=option,
                        package_id=package_id,
                        defaults={
                            'pricing_type': pricing_type,
                            'value': value
                        }
                    )
                    
                    if not created:
                        pricing.pricing_type = pricing_type
                        pricing.value = value
                        pricing.save()

            return Response({'message': 'Option pricing rules updated successfully'})
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class QuestionTreeView(APIView):
    """Get the complete question tree for a service"""
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id):
        try:
            service = get_object_or_404(Service, id=service_id, is_active=True)
            
            # Get root questions (no parent)
            root_questions = Question.objects.filter(
                service=service,
                is_active=True,
                parent_question__isnull=True
            ).prefetch_related(
                'options__pricing_rules',
                'sub_questions__pricing_rules',
                'pricing_rules',
                'child_questions__options',
                'child_questions__sub_questions',
                'child_questions__pricing_rules'
            ).order_by('order')
            
            serializer = QuestionSerializer(root_questions, many=True, context={'request': request})
            
            return Response({
                'service': {
                    'id': service.id,
                    'name': service.name,
                    'description': service.description
                },
                'questions': serializer.data
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class ConditionalQuestionsView(APIView):
    """Get conditional questions based on parent question and answer"""
    permission_classes = [IsAuthenticated]

    def get(self, request, parent_question_id):
        answer = request.query_params.get('answer')
        option_id = request.query_params.get('option_id')
        
        if not answer and not option_id:
            return Response({'error': 'Either answer or option_id is required'}, 
                          status=status.HTTP_400_BAD_REQUEST)
        
        try:
            parent_question = get_object_or_404(Question, id=parent_question_id)
            
            # Build filter for conditional questions
            filter_kwargs = {
                'parent_question': parent_question,
                'is_active': True
            }
            
            if answer:
                filter_kwargs['condition_answer'] = answer
            if option_id:
                filter_kwargs['condition_option_id'] = option_id
            
            conditional_questions = Question.objects.filter(**filter_kwargs).prefetch_related(
                'options__pricing_rules',
                'sub_questions__pricing_rules',
                'pricing_rules'
            ).order_by('order')
            
            serializer = QuestionSerializer(conditional_questions, many=True, context={'request': request})
            
            return Response({
                'parent_question_id': parent_question_id,
                'condition': {
                    'answer': answer,
                    'option_id': option_id
                },
                'conditional_questions': serializer.data
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        

class QuestionResponseListCreateView(generics.ListCreateAPIView):
    """List and create question responses"""
    queryset = QuestionResponse.objects.prefetch_related(
        'option_responses__option',
        'sub_question_responses__sub_question'
    )
    serializer_class = QuestionResponseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        question_id = self.request.query_params.get('question', None)
        if question_id:
            queryset = queryset.filter(question_id=question_id)
        return queryset.order_by('-created_at')




# Analytics Views
class ServiceAnalyticsView(APIView):
    """Get analytics data for services"""
    permission_classes = [IsAdminPermission]

    def get(self, request):
        services = Service.objects.filter(is_active=True).annotate(
            total_packages=Count('packages', filter=models.Q(packages__is_active=True)),
            total_features=Count('features', filter=models.Q(features__is_active=True)),
            total_questions=Count('questions', filter=models.Q(questions__is_active=True)),
            average_package_price=Avg('packages__base_price', filter=models.Q(packages__is_active=True))
        ).order_by('order', 'name')

        analytics_data = []
        for service in services:
            analytics_data.append({
                'service_id': service.id,
                'service_name': service.name,
                'total_packages': service.total_packages or 0,
                'total_features': service.total_features or 0,
                'total_questions': service.total_questions or 0,
                'average_package_price': service.average_package_price or Decimal('0.00'),
                'created_at': service.created_at
            })

        serializer = ServiceAnalyticsSerializer(analytics_data, many=True)
        return Response(serializer.data)


# Utility Views
class PricingCalculatorView(APIView):
    """Calculate pricing based on question responses"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PricingCalculationSerializer(data=request.data)
        if serializer.is_valid():
            service_id = serializer.validated_data['service_id']
            package_id = serializer.validated_data['package_id']
            responses = serializer.validated_data['responses']

            try:
                service = get_object_or_404(Service, id=service_id)
                # package = get_object_or_404(Package, id=package_id)
                
                total_adjustment = Decimal('0.00')
                breakdown = []

                for response in responses:
                    question_id = response['question_id']
                    question = get_object_or_404(Question, id=question_id)
                    
                    question_adjustment = Decimal('0.00')
                    question_breakdown = {
                        'question_id': question_id,
                        'question_text': question.question_text,
                        'question_type': question.question_type,
                        'adjustments': []
                    }

                    if question.question_type == 'yes_no':
                        if response.get('yes_no_answer') is True:
                            pricing = QuestionPricing.objects.filter(
                                question=question, package_id=package_id
                            ).first()
                            if pricing and pricing.yes_pricing_type != 'ignore':
                                question_adjustment += pricing.yes_value
                                question_breakdown['adjustments'].append({
                                    'type': 'yes_answer',
                                    'pricing_type': pricing.yes_pricing_type,
                                    'value': pricing.yes_value
                                })

                    elif question.question_type in ['describe', 'quantity']:
                        selected_options = response.get('selected_options', [])
                        for option_data in selected_options:
                            option_id = option_data['option_id']
                            quantity = option_data.get('quantity', 1)
                            
                            pricing = OptionPricing.objects.filter(
                                option_id=option_id, package_id=package_id
                            ).first()
                            
                            if pricing and pricing.pricing_type != 'ignore':
                                if pricing.pricing_type == 'per_quantity':
                                    adjustment = pricing.value * quantity
                                else:
                                    adjustment = pricing.value
                                    
                                question_adjustment += adjustment
                                question_breakdown['adjustments'].append({
                                    'type': 'option_selection',
                                    'option_id': option_id,
                                    'quantity': quantity,
                                    'pricing_type': pricing.pricing_type,
                                    'value': adjustment
                                })

                    elif question.question_type == 'multiple_yes_no':
                        sub_question_answers = response.get('sub_question_answers', [])
                        for sub_answer in sub_question_answers:
                            if sub_answer.get('answer') is True:
                                sub_question_id = sub_answer['sub_question_id']
                                pricing = SubQuestionPricing.objects.filter(
                                    sub_question_id=sub_question_id, package_id=package_id
                                ).first()
                                
                                if pricing and pricing.yes_pricing_type != 'ignore':
                                    question_adjustment += pricing.yes_value
                                    question_breakdown['adjustments'].append({
                                        'type': 'sub_question_yes',
                                        'sub_question_id': sub_question_id,
                                        'pricing_type': pricing.yes_pricing_type,
                                        'value': pricing.yes_value
                                    })

                    total_adjustment += question_adjustment
                    question_breakdown['total_adjustment'] = question_adjustment
                    breakdown.append(question_breakdown)

                return Response({
                    'service_id': service_id,
                    'package_id': package_id,
                    'total_adjustment': total_adjustment,
                    'breakdown': breakdown
                })
                
            except Exception as e:
                return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        


class ServiceSettingsView(APIView):
    def get(self, request, service_id):
        service = get_object_or_404(Service, id=service_id)
        try:
            settings = service.settings
            serializer = ServiceSettingsSerializer(settings)
            return Response(serializer.data)
        except ServiceSettings.DoesNotExist:
            return Response({"detail": "Settings not found."}, status=status.HTTP_404_NOT_FOUND)
    def post(self, request, service_id):
        service = get_object_or_404(Service, id=service_id)

        serializer = ServiceSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        settings, created = ServiceSettings.objects.update_or_create(
            service=service,
            defaults=serializer.validated_data
        )

        return Response(
            ServiceSettingsSerializer(settings).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    def put(self, request, service_id):
        service = get_object_or_404(Service, id=service_id)
        settings = get_object_or_404(ServiceSettings, service=service)

        serializer = ServiceSettingsSerializer(settings, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Who am I (current user profile)
class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
    



from .serializers import GlobalSizePackageSerializer, ServicePackageSizeMappingSerializer
from .models import GlobalSizePackage, ServicePackageSizeMapping

class GlobalSizePackageListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/global-sizes/ → List all global size packages with templates
    POST /api/global-sizes/ → Create a global size package with template prices
    """
    serializer_class = GlobalSizePackageSerializer
    queryset = GlobalSizePackage.objects.all().prefetch_related('template_prices')

class AutoMapGlobalToServicePackages(APIView):
    """
    POST /api/services/{service_id}/auto-map-packages/
    Automatically map global pricing templates to service-level packages
    by order.
    """
    def post(self, request, service_id):
        try:
            service = Service.objects.prefetch_related('packages').get(id=service_id)
        except Service.DoesNotExist:
            return Response({'detail': 'Service not found'}, status=404)

        global_sizes = GlobalSizePackage.objects.prefetch_related('template_prices').order_by('order')
        service_packages = list(service.packages.filter(is_active=True).order_by('order'))

        if not service_packages:
            return Response({'detail': 'No service-level packages found.'}, status=400)

        created_mappings = []
        for global_size in global_sizes:
            templates = list(global_size.template_prices.order_by('order'))
            for idx, template in enumerate(templates):
                if idx < len(service_packages):
                    service_package = service_packages[idx]
                    mapping, created = ServicePackageSizeMapping.objects.get_or_create(
                        service_package=service_package,
                        global_size=global_size,
                        defaults={'price': template.price}
                    )
                    if created:
                        created_mappings.append(mapping)

        return Response(ServicePackageSizeMappingSerializer(created_mappings, many=True).data, status=201)
    

from rest_framework.generics import ListAPIView

class ServiceMappedSizesAPIView(ListAPIView):
    """
    GET /api/services/{service_id}/mapped-sizes/

    Retrieve all size mappings with prices for a given service.
    """
    serializer_class = ServicePackageSizeMappingSerializer

    def get_queryset(self):
        service_id = self.kwargs['service_id']
        return ServicePackageSizeMapping.objects.filter(
            service_package__service_id=service_id
        ).select_related('global_size', 'service_package')
    


class GlobalSizePackageDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = GlobalSizePackageSerializer
    queryset = GlobalSizePackage.objects.all().prefetch_related('template_prices')
    lookup_field = 'id'





class GlobalSettingsView(APIView):
    """
    GET → Retrieve global base price
    PUT → Update global base price
    """

    def get(self, request):
        settings, _ = GlobalBasePrice.objects.get_or_create(id=1)
        serializer = GlobalBasePriceSerializer(settings)
        return Response(serializer.data)

    def put(self, request):
        settings, _ = GlobalBasePrice.objects.get_or_create(id=1)
        serializer = GlobalBasePriceSerializer(settings, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================
# User CRUD (Admin managed)
# ============================
class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAdminPermission]

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get('search')
        role = self.request.query_params.get('role')
        if search:
            qs = qs.filter(
                models.Q(username__icontains=search) |
                models.Q(email__icontains=search) |
                models.Q(first_name__icontains=search) |
                models.Q(last_name__icontains=search)
            )
        if role:
            qs = qs.filter(role=role)
        return qs


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_permissions(self):
        if self.request.method in ['PUT', 'PATCH', 'DELETE', 'POST']:
            return [IsAdminPermission()]
        return [permissions.IsAuthenticated()]

    def get_object(self):
        obj = super().get_object()
        user = self.request.user
        if self.request.method == 'GET':
            if user.is_authenticated and (user.is_admin or user.id == obj.id):
                return obj
            raise permissions.PermissionDenied('Not allowed')
        return obj