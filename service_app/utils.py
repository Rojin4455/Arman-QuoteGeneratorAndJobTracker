from decimal import Decimal
from typing import Dict, List, Any
from .models import Package, Question, QuestionOption, QuestionPricing, OptionPricing, Location

class PricingCalculator:
    """Utility class for calculating pricing based on answers"""
    
    @staticmethod
    def calculate_price(package_id: str, location_id: str = None, answers: List[Dict] = None) -> Dict[str, Any]:
        """
        Calculate total price for a package with given answers
        
        Args:
            package_id: UUID of the package
            location_id: UUID of the location (optional)
            answers: List of answers in format:
                [
                    {'question_id': 'uuid', 'answer': True},  # For yes/no
                    {'question_id': 'uuid', 'option_id': 'uuid'}  # For options
                ]
        
        Returns:
            Dict with price breakdown
        """
        try:
            package = Package.objects.get(id=package_id)
            location = None
            if location_id:
                location = Location.objects.get(id=location_id)
            
            base_price = package.base_price
            trip_surcharge = location.trip_surcharge if location else Decimal('0.00')
            question_adjustments = Decimal('0.00')
            adjustment_details = []
            
            PERCENT_OF_TOTAL = ('upcharge_percent_of_total', 'discount_percent_of_total')
            fixed_sum = Decimal('0.00')
            percent_entries = []

            if answers:
                for answer in answers:
                    question_id = answer.get('question_id')
                    question = Question.objects.get(id=question_id)

                    if question.question_type == 'yes_no':
                        yes_answer = answer.get('answer', False)
                        if yes_answer:
                            try:
                                pricing = QuestionPricing.objects.get(
                                    question=question, package=package
                                )
                                if pricing.yes_pricing_type in PERCENT_OF_TOTAL:
                                    sign = 1 if pricing.yes_pricing_type == 'upcharge_percent_of_total' else -1
                                    percent_entries.append((sign, pricing.yes_value))
                                else:
                                    adj = PricingCalculator._calculate_adjustment(
                                        base_price, pricing.yes_pricing_type, pricing.yes_value
                                    )
                                    fixed_sum += adj
                                    adjustment_details.append({
                                        'question': question.question_text,
                                        'answer': 'Yes',
                                        'adjustment': adj,
                                        'type': pricing.yes_pricing_type
                                    })
                            except QuestionPricing.DoesNotExist:
                                pass

                    elif question.question_type == 'options':
                        option_id = answer.get('option_id')
                        if option_id:
                            try:
                                option = QuestionOption.objects.get(id=option_id)
                                pricing = OptionPricing.objects.get(
                                    option=option, package=package
                                )
                                if pricing.pricing_type in PERCENT_OF_TOTAL:
                                    sign = 1 if pricing.pricing_type == 'upcharge_percent_of_total' else -1
                                    percent_entries.append((sign, pricing.value))
                                else:
                                    adj = PricingCalculator._calculate_adjustment(
                                        base_price, pricing.pricing_type, pricing.value
                                    )
                                    fixed_sum += adj
                                    adjustment_details.append({
                                        'question': question.question_text,
                                        'answer': option.option_text,
                                        'adjustment': adj,
                                        'type': pricing.pricing_type
                                    })
                            except (QuestionOption.DoesNotExist, OptionPricing.DoesNotExist):
                                pass

                subtotal = base_price + trip_surcharge + fixed_sum
                for sign, value in percent_entries:
                    pct_adj = subtotal * (Decimal(sign) * value / Decimal('100'))
                    fixed_sum += pct_adj
                    adjustment_details.append({
                        'question': '',
                        'answer': '% of package total',
                        'adjustment': pct_adj,
                        'type': 'upcharge_percent_of_total' if sign == 1 else 'discount_percent_of_total'
                    })

            question_adjustments = fixed_sum
            total_price = base_price + trip_surcharge + question_adjustments
            
            return {
                'base_price': base_price,
                'trip_surcharge': trip_surcharge,
                'question_adjustments': question_adjustments,
                'total_price': total_price,
                'adjustment_details': adjustment_details,
                'package_name': package.name,
                'location_name': location.name if location else None
            }
            
        except Exception as e:
            raise ValueError(f"Error calculating price: {str(e)}")
    
    @staticmethod
    def _calculate_adjustment(base_price: Decimal, pricing_type: str, value: Decimal) -> Decimal:
        """Calculate price adjustment (fixed and % of base). Percent-of-total handled in caller."""
        if pricing_type in ('upcharge_percent_of_total', 'discount_percent_of_total'):
            return Decimal('0.00')
        if pricing_type == 'upcharge_percent':
            return base_price * (value / Decimal('100'))
        if pricing_type == 'discount_percent':
            return -(base_price * (value / Decimal('100')))
        if pricing_type == 'fixed_price':
            return value
        return Decimal('0.00')


class DataValidator:
    """Utility class for data validation"""
    
    @staticmethod
    def validate_package_data(data: Dict) -> List[str]:
        """Validate package data"""
        errors = []
        
        if not data.get('name'):
            errors.append("Package name is required")
        
        if not data.get('base_price'):
            errors.append("Base price is required")
        else:
            try:
                price = Decimal(str(data['base_price']))
                if price < 0:
                    errors.append("Base price cannot be negative")
            except:
                errors.append("Invalid base price format")
        
        return errors
    
    @staticmethod
    def validate_question_data(data: Dict) -> List[str]:
        """Validate question data"""
        errors = []
        
        if not data.get('question_text'):
            errors.append("Question text is required")
        
        if not data.get('question_type'):
            errors.append("Question type is required")
        elif data.get('question_type') not in ['yes_no', 'options']:
            errors.append("Invalid question type")
        
        if data.get('question_type') == 'options':
            options = data.get('options', [])
            if len(options) < 2:
                errors.append("Options questions must have at least 2 options")
        
        return errors


# Custom exceptions
class PricingCalculationError(Exception):
    """Raised when there's an error in pricing calculation"""
    pass

class ValidationError(Exception):
    """Raised when data validation fails"""
    pass