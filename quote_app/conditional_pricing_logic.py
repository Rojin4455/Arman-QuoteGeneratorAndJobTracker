# conditional_pricing_logic.py - Pricing calculations for conditional questions
from decimal import Decimal

from service_app.models import QuestionPricing, OptionPricing, SubQuestionPricing

"""
CONDITIONAL QUESTION PRICING LOGIC
==================================

Conditional questions have their own pricing rules that are applied
when the condition is met and the question is answered.

Example Pricing Setup in Admin:
1. Parent Question: "What type of cleaning?" 
   - Deep Cleaning option selected → triggers conditional question
   
2. Conditional Question: "How many rooms need deep cleaning?"
   - Each room adds $25 to the package price

Pricing Calculation Flow:
"""

def calculate_conditional_question_pricing(service_selection, package):
    """Calculate pricing for conditional questions"""
    total_adjustment = Decimal('0.00')
    
    for question_response in service_selection.question_responses.all():
        question = question_response.question
        
        # Skip if not a conditional question
        if not question.parent_question:
            continue
            
        # Verify the condition was actually met
        parent_response = service_selection.question_responses.filter(
            question=question.parent_question
        ).first()
        
        if not parent_response:
            continue
            
        condition_met = check_condition_met(question, parent_response)
        if not condition_met:
            continue
            
        # Calculate pricing based on question type
        if question.question_type == 'yes_no':
            if question_response.yes_no_answer is True:
                pricing = QuestionPricing.objects.filter(
                    question=question, package=package
                ).first()
                if pricing and pricing.yes_pricing_type != 'ignore':
                    if pricing.yes_pricing_type in ('upcharge_percent_of_total', 'discount_percent_of_total'):
                        continue  # percent-of-total needs subtotal; not supported in this flow
                    if pricing.yes_pricing_type == 'discount_percent':
                        total_adjustment -= pricing.yes_value
                    else:
                        total_adjustment += pricing.yes_value

        elif question.question_type in ['describe', 'quantity']:
            for option_response in question_response.option_responses.all():
                pricing = OptionPricing.objects.filter(
                    option=option_response.option, package=package
                ).first()
                if pricing and pricing.pricing_type != 'ignore':
                    if pricing.pricing_type in ('upcharge_percent_of_total', 'discount_percent_of_total'):
                        continue
                    if pricing.pricing_type == 'per_quantity':
                        total_adjustment += pricing.value * option_response.quantity
                    elif pricing.pricing_type == 'discount_percent':
                        total_adjustment -= pricing.value
                    else:
                        total_adjustment += pricing.value

        elif question.question_type == 'multiple_yes_no':
            for sub_response in question_response.sub_question_responses.all():
                if sub_response.answer is True:
                    pricing = SubQuestionPricing.objects.filter(
                        sub_question=sub_response.sub_question, package=package
                    ).first()
                    if pricing and pricing.yes_pricing_type != 'ignore':
                        if pricing.yes_pricing_type in ('upcharge_percent_of_total', 'discount_percent_of_total'):
                            continue
                        if pricing.yes_pricing_type == 'discount_percent':
                            total_adjustment -= pricing.yes_value
                        else:
                            total_adjustment += pricing.yes_value
    
    return total_adjustment

def check_condition_met(conditional_question, parent_response):
    """Check if the condition for showing the conditional question was met"""
    parent_question = conditional_question.parent_question
    
    if parent_question.question_type == 'yes_no':
        expected_answer = conditional_question.condition_answer
        actual_answer = 'yes' if parent_response.yes_no_answer else 'no'
        return expected_answer == actual_answer
    
    elif parent_question.question_type in ['describe', 'quantity']:
        expected_option_id = conditional_question.condition_option_id
        selected_options = parent_response.option_responses.all()
        selected_option_ids = [opt.option_id for opt in selected_options]
        return expected_option_id in selected_option_ids
    
    return False

"""
PRICING EXAMPLE:

Service: House Cleaning
Package: Premium Package ($150 base)

Questions & Responses:
1. "What type of cleaning?" → "Deep Cleaning" selected
   - Pricing: +$50 for deep cleaning option
   
2. Conditional: "How many rooms?" → "3 rooms" selected  
   - Pricing: +$25 per room = +$75
   - Only applies because "Deep Cleaning" was selected

3. "Do you have pets?" → "Yes"
   - Pricing: +$20 for pet cleaning
   
4. Conditional: "How many pets?" → "2 pets"
   - Pricing: +$10 per pet = +$20
   - Only applies because "Yes" was answered for pets

Final Calculation:
- Base Package: $150
- Deep Cleaning: +$50
- 3 Rooms (conditional): +$75  
- Pet Cleaning: +$20
- 2 Pets (conditional): +$20
- Total: $315
"""
