# views.py
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from decimal import Decimal

from .models import (
    Service, Package, Question, Quote, 
    QuoteQuestionAnswer, QuestionOption,
)
from .serializers import (
    ContactSerializer, ServiceListSerializer, ServiceSerializer,
    QuestionSerializer, QuoteSerializer, QuoteCreateSerializer,QuestionWithPricingSerializer
)
from .utils import calculate_total_quote_price
from service_app.serializers import PackageSerializer
from .utils import create_ghl_contact_and_note
from service_app.models import QuestionPricing, OptionPricing
from rest_framework.views import APIView
from accounts.models import Contact





# Step 1: Submit Contact Info
class ContactCreateView(generics.CreateAPIView):
    """Create a new contact"""
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [AllowAny]


class ContactCreateUpdateView(generics.RetrieveUpdateAPIView):
    """Retrieve or update a contact"""
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'


# Step 2: List All Services
class ServiceListView(generics.ListAPIView):
    """List all active services"""
    queryset = Service.objects.filter(is_active=True)
    serializer_class = ServiceListSerializer
    permission_classes = [AllowAny]


# Step 3: Get Service Details with Packages
class ServiceDetailView(generics.RetrieveAPIView):
    """Get service details with all packages and features"""
    queryset = Service.objects.filter(is_active=True)
    serializer_class = ServiceSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'


# Step 4: Get Package Details (Optional - if you need individual package info)
class PackageDetailView(generics.RetrieveAPIView):
    """Get package details"""
    queryset = Package.objects.filter(is_active=True)
    serializer_class = PackageSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'


# Step 5: Get Questions for a Service
class ServiceQuestionsView(generics.ListAPIView):
    """Get all questions for a specific service with package-specific pricing"""
    serializer_class = QuestionWithPricingSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        service_id = self.kwargs['service_id']
        return Question.objects.filter(
            service_id=service_id, 
            is_active=True
        ).order_by('order', 'created_at')
    
    def list(self, request, *args, **kwargs):
        # Get package_id from query params
        package_id = request.query_params.get('package_id')
        if not package_id:
            return Response(
                {'error': 'package_id query parameter is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate package exists and belongs to service
        service_id = self.kwargs['service_id']
        try:
            package = Package.objects.get(
                id=package_id, 
                service_id=service_id, 
                is_active=True
            )
        except Package.DoesNotExist:
            return Response(
                {'error': 'Package not found or does not belong to this service'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        queryset = self.get_queryset()
        questions_data = []
        
        for question in queryset:
            question_dict = {
                'id': str(question.id),
                'question_text': question.question_text,
                'question_type': question.question_type,
                'order': question.order
            }
            
            if question.question_type == 'yes_no':
                # Get yes/no pricing for this package
                try:
                    question_pricing = QuestionPricing.objects.get(
                        question=question, 
                        package=package
                    )
                    question_dict['yes_pricing_type'] = question_pricing.yes_pricing_type
                    question_dict['yes_value'] = str(question_pricing.yes_value)
                except QuestionPricing.DoesNotExist:
                    question_dict['yes_pricing_type'] = 'ignore'
                    question_dict['yes_value'] = '0.00'
                
                # No options for yes/no questions
                question_dict['options'] = []
                
            elif question.question_type == 'options':
                # Get options with pricing, exclude those marked as 'ignore'
                options_data = []
                
                for option in question.options.all():
                    try:
                        option_pricing = OptionPricing.objects.get(
                            option=option, 
                            package=package
                        )

                        print("option_pricing.pricing_type: ", option_pricing.pricing_type)
                        
                        # Only include options that are not set to 'ignore'
                        if option_pricing.pricing_type != 'ignore':
                            options_data.append({
                                'id': str(option.id),
                                'option_text': option.option_text,
                                'order': option.order,
                                'pricing_type': option_pricing.pricing_type,
                                'value': str(option_pricing.value)
                            })
                            
                    except OptionPricing.DoesNotExist:
                        # If no pricing rule exists, treat as 'ignore' and exclude
                        continue
                
                question_dict['options'] = options_data
                
                # Remove yes/no pricing fields for options questions
                question_dict.pop('yes_pricing_type', None)
                question_dict.pop('yes_value', None)
            
            questions_data.append(question_dict)
        
        return Response(questions_data)


# Step 6: Create Quote (Checkout Summary)
class QuoteCreateView(generics.CreateAPIView):
    """Create a quote with all selections and calculate pricing"""
    serializer_class = QuoteCreateSerializer
    permission_classes = [AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        validated_data = serializer.validated_data
        contact = validated_data['contact']
        service = validated_data['service']
        package = validated_data['package']
        answers_data = validated_data.get('answers', [])
        
        # Calculate pricing
        price_breakdown = calculate_total_quote_price(
            contact, package, answers_data
        )
        
        # Create quote
        quote = Quote.objects.create(
            contact=contact,
            service=service,
            package=package,
            nearest_location=price_breakdown['nearest_location'],
            distance_to_location=price_breakdown['distance_to_location'],
            base_price=price_breakdown['base_price'],
            trip_surcharge=price_breakdown['trip_surcharge'],
            question_adjustments=price_breakdown['question_adjustments'],
            total_price=price_breakdown['total_price'],
            status='draft'
        )
        
        # Create question answers
        for answer_data in answers_data:
            question_id = answer_data['question_id']
            
            try:
                question = service.questions.get(id=question_id, is_active=True)
            except Question.DoesNotExist:
                continue
            
            # Calculate individual price adjustment for this answer
            price_adjustment = Decimal('0.00')
            selected_option = None
            yes_no_answer = None
            
            if question.question_type == 'yes_no':
                yes_no_answer = answer_data.get('yes_no_answer', False)
                from .utils import calculate_question_price_adjustment
                price_adjustment = calculate_question_price_adjustment(
                    question, yes_no_answer, package
                )
                
            elif question.question_type == 'options':
                option_id = answer_data.get('selected_option_id')
                if option_id:
                    try:
                        selected_option = question.options.get(id=option_id, is_active=True)
                        from .utils import calculate_option_price_adjustment
                        price_adjustment = calculate_option_price_adjustment(
                            selected_option, package
                        )
                    except QuestionOption.DoesNotExist:
                        continue
            
            QuoteQuestionAnswer.objects.create(
                quote=quote,
                question=question,
                yes_no_answer=yes_no_answer,
                selected_option=selected_option,
                price_adjustment=price_adjustment
            )
        
        create_ghl_contact_and_note(contact, quote)

        # Return quote details
        quote_serializer = QuoteSerializer(quote)
        return Response(quote_serializer.data, status=status.HTTP_201_CREATED)


# Get Quote Details
class QuoteDetailView(generics.RetrieveAPIView):
    """Get quote details"""
    queryset = Quote.objects.all()
    serializer_class = QuoteSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'


# Update Quote Status (for admin use or future features)
@api_view(['PATCH'])
@permission_classes([AllowAny])
def update_quote_status(request, quote_id):
    """Update quote status"""
    quote = get_object_or_404(Quote, id=quote_id)
    
    new_status = request.data.get('status')
    if new_status not in dict(Quote.STATUS_CHOICES):
        return Response(
            {'error': 'Invalid status'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    quote.status = new_status
    quote.save()
    
    serializer = QuoteSerializer(quote)
    return Response(serializer.data)


# Price Calculator Endpoint (Optional - for real-time price updates)
@api_view(['POST'])
@permission_classes([AllowAny])
def calculate_price(request):
    """Calculate price without creating a quote"""
    serializer = QuoteCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    
    validated_data = serializer.validated_data
    contact = validated_data['contact']
    package = validated_data['package']
    answers_data = validated_data.get('answers', [])
    
    price_breakdown = calculate_total_quote_price(
        contact, package, answers_data
    )
    
    return Response({
        'base_price': str(price_breakdown['base_price']),
        'trip_surcharge': str(price_breakdown['trip_surcharge']),
        'question_adjustments': str(price_breakdown['question_adjustments']),
        'total_price': str(price_breakdown['total_price']),
        'nearest_location': price_breakdown['nearest_location'].name if price_breakdown['nearest_location'] else None,
        'distance_to_location': str(price_breakdown['distance_to_location']) if price_breakdown['distance_to_location'] else None
    })


# Get Contact's Quotes
class ContactQuotesView(generics.ListAPIView):
    """Get all quotes for a contact"""
    serializer_class = QuoteSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        contact_id = self.kwargs['contact_id']
        return Quote.objects.filter(contact_id=contact_id)
    


class QuestionTreeView(APIView):
    """Get the complete question tree for a service"""
    permission_classes = [AllowAny]

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