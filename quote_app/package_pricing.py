from decimal import Decimal

from service_app.models import QuestionPricing, OptionPricing, SubQuestionPricing

PERCENT_OF_TOTAL = ('upcharge_percent_of_total', 'discount_percent_of_total')


def _percent_entry(pricing_type, value, question_text, answer_text):
    return {
        'sign': 1 if pricing_type == 'upcharge_percent_of_total' else -1,
        'percent': value,
        'question_text': question_text,
        'answer_text': answer_text,
    }


def compute_package_question_adjustments(service_selection, package, base_price, sqft_price, surcharge_amount):
    """Question adjustments for one package. Two-pass: (1) fixed adjustments, (2) % of package subtotal.

    Returns {'total', 'subtotal', 'percent_lines'}; each percent line carries the question/answer that
    triggered it and the dollar amount it added (or removed) from the package.
    """
    fixed_sum = Decimal('0.00')
    percent_entries = []

    for question_response in service_selection.question_responses.all():
        question = question_response.question
        question_fixed = Decimal('0.00')

        if question.question_type == 'yes_no':
            if question_response.yes_no_answer is True:
                pricing = QuestionPricing.objects.filter(
                    question=question, package=package
                ).first()
                if pricing and pricing.yes_pricing_type != 'ignore':
                    if pricing.yes_pricing_type in PERCENT_OF_TOTAL:
                        percent_entries.append(_percent_entry(
                            pricing.yes_pricing_type, pricing.yes_value, question.question_text, 'Yes'
                        ))
                    elif pricing.yes_pricing_type == 'upcharge_percent':
                        question_fixed += pricing.yes_value
                    elif pricing.yes_pricing_type == 'discount_percent':
                        question_fixed -= pricing.yes_value
                    elif pricing.yes_pricing_type == 'fixed_price':
                        question_fixed += pricing.yes_value

        elif question.question_type in ['describe', 'quantity']:
            for option_response in question_response.option_responses.all():
                pricing = OptionPricing.objects.filter(
                    option=option_response.option, package=package
                ).first()
                if not pricing or pricing.pricing_type == 'ignore':
                    continue
                if pricing.pricing_type in PERCENT_OF_TOTAL:
                    percent_entries.append(_percent_entry(
                        pricing.pricing_type, pricing.value, question.question_text, option_response.option.option_text
                    ))
                    continue
                if question.question_type == 'quantity':
                    if pricing.pricing_type == 'discount_percent':
                        question_fixed -= pricing.value * option_response.quantity
                    elif pricing.pricing_type == 'upcharge_percent':
                        question_fixed += pricing.value * option_response.quantity
                    elif pricing.pricing_type == 'per_quantity':
                        question_fixed += pricing.value * option_response.quantity
                    elif pricing.pricing_type == 'fixed_price':
                        question_fixed += pricing.value * option_response.quantity
                elif question.question_type == 'describe':
                    if pricing.pricing_type == 'per_quantity':
                        question_fixed += pricing.value * option_response.quantity
                    elif pricing.pricing_type == 'upcharge_percent':
                        question_fixed += pricing.value
                    elif pricing.pricing_type == 'discount_percent':
                        question_fixed -= pricing.value
                    elif pricing.pricing_type == 'fixed_price':
                        question_fixed += pricing.value

        elif question.question_type == 'multiple_yes_no':
            for sub_response in question_response.sub_question_responses.all():
                if sub_response.answer is not True:
                    continue
                pricing = SubQuestionPricing.objects.filter(
                    sub_question=sub_response.sub_question, package=package
                ).first()
                if not pricing or pricing.yes_pricing_type == 'ignore':
                    continue
                if pricing.yes_pricing_type in PERCENT_OF_TOTAL:
                    percent_entries.append(_percent_entry(
                        pricing.yes_pricing_type, pricing.yes_value,
                        question.question_text, sub_response.sub_question.sub_question_text
                    ))
                elif pricing.yes_pricing_type == 'upcharge_percent':
                    question_fixed += pricing.yes_value
                elif pricing.yes_pricing_type == 'discount_percent':
                    question_fixed -= pricing.yes_value
                elif pricing.yes_pricing_type == 'fixed_price':
                    question_fixed += pricing.yes_value

        fixed_sum += question_fixed

    # Subtotal = base + sqft + surcharge + all fixed adjustments
    subtotal = base_price + sqft_price + surcharge_amount + fixed_sum
    percent_sum = Decimal('0.00')
    percent_lines = []
    for entry in percent_entries:
        amount = subtotal * (Decimal(entry['sign']) * entry['percent'] / Decimal('100'))
        percent_sum += amount
        percent_lines.append({
            'question_text': entry['question_text'],
            'answer_text': entry['answer_text'],
            'percent': entry['percent'],
            'amount': amount.quantize(Decimal('0.01')),
        })

    return {
        'total': fixed_sum + percent_sum,
        'subtotal': subtotal.quantize(Decimal('0.01')),
        'percent_lines': percent_lines,
    }
