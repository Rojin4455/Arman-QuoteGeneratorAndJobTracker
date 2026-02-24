from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from decimal import Decimal
from service_app.models import Service, Package, Feature, PackageFeature, Question, QuestionOption, QuestionPricing, OptionPricing

User = get_user_model()

class Command(BaseCommand):
    help = 'Seed database with sample data'

    def handle(self, *args, **options):
        # Get or create admin user
        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@example.com',
                'is_admin': True,
                'is_staff': True,
                'is_superuser': True
            }
        )
        if created:
            admin_user.set_password('adminuser@246!')
            admin_user.save()

        # Create Window Cleaning Service
        service, created = Service.objects.get_or_create(
            name='Window Cleaning',
            defaults={
                'description': 'Professional window cleaning services for residential and commercial properties',
                'created_by': admin_user,
                'order': 1
            }
        )

        if created:
            self.stdout.write(f'Created service: {service.name}')

            # Create packages
            packages_data = [
                {'name': 'Basic', 'base_price': Decimal('50.00'), 'order': 1},
                {'name': 'Standard', 'base_price': Decimal('75.00'), 'order': 2},
                {'name': 'Premium', 'base_price': Decimal('100.00'), 'order': 3},
                {'name': 'Deluxe', 'base_price': Decimal('150.00'), 'order': 4},
            ]

            packages = []
            for pkg_data in packages_data:
                package = Package.objects.create(
                    service=service,
                    **pkg_data
                )
                packages.append(package)
                self.stdout.write(f'Created package: {package.name}')

            # Create features
            features_data = [
                {'name': 'Interior Cleaning', 'description': 'Clean windows from inside'},
                {'name': 'Exterior Cleaning', 'description': 'Clean windows from outside'},
                {'name': 'Screen Cleaning', 'description': 'Clean window screens'},
                {'name': 'Sill Cleaning', 'description': 'Clean window sills'},
                {'name': 'Frame Cleaning', 'description': 'Clean window frames'},
                {'name': 'Post-Construction Cleanup', 'description': 'Remove construction debris and stickers'},
            ]

            features = []
            for feat_data in features_data:
                feature = Feature.objects.create(
                    service=service,
                    **feat_data
                )
                features.append(feature)
                self.stdout.write(f'Created feature: {feature.name}')

            # Assign features to packages
            feature_assignments = [
                # Basic package
                {'package': packages[0], 'features': [0, 1]},  # Interior, Exterior
                # Standard package  
                {'package': packages[1], 'features': [0, 1, 2]},  # Interior, Exterior, Screen
                # Premium package
                {'package': packages[2], 'features': [0, 1, 2, 3]},  # Interior, Exterior, Screen, Sill
                # Deluxe package
                {'package': packages[3], 'features': [0, 1, 2, 3, 4, 5]},  # All features
            ]

            for assignment in feature_assignments:
                package = assignment['package']
                for feature_idx in assignment['features']:
                    PackageFeature.objects.create(
                        package=package,
                        feature=features[feature_idx],
                        is_included=True
                    )

            # Create sample questions
            # Yes/No Question
            question1 = Question.objects.create(
                service=service,
                question_text="Do your windows have tape, stickers, or paint from renovations?",
                question_type='yes_no',
                order=1
            )

            # Create pricing rules for yes/no question
            pricing_rules_yn = [
                {'package': packages[0], 'type': 'upcharge_percent', 'value': Decimal('10.00')},
                {'package': packages[1], 'type': 'discount_percent', 'value': Decimal('20.00')},
                {'package': packages[2], 'type': 'ignore', 'value': Decimal('0.00')},
                {'package': packages[3], 'type': 'fixed_price', 'value': Decimal('20.00')},
            ]

            for rule in pricing_rules_yn:
                QuestionPricing.objects.create(
                    question=question1,
                    package=rule['package'],
                    yes_pricing_type=rule['type'],
                    yes_value=rule['value']
                )

            # Multiple Choice Question
            question2 = Question.objects.create(
                service=service,
                question_text="How many window panes would you like us to clean?",
                question_type='options',
                order=2
            )

            # Create options
            options_data = [
                {'text': 'Small (1-10 panes)', 'order': 1},
                {'text': 'Medium (11-25 panes)', 'order': 2},
                {'text': 'Large (26-50 panes)', 'order': 3},
                {'text': 'Extra Large (50+ panes)', 'order': 4},
            ]

            options = []
            for opt_data in options_data:
                option = QuestionOption.objects.create(
                    question=question2,
                    option_text=opt_data['text'],
                    order=opt_data['order']
                )
                options.append(option)

            # Create option pricing rules for each package
            option_pricing_rules = [
                # Small option
                [
                    {'package': packages[0], 'type': 'ignore', 'value': Decimal('0.00')},
                    {'package': packages[1], 'type': 'discount_percent', 'value': Decimal('5.00')},
                    {'package': packages[2], 'type': 'ignore', 'value': Decimal('0.00')},
                    {'package': packages[3], 'type': 'discount_percent', 'value': Decimal('10.00')},
                ],
                # Medium option
                [
                    {'package': packages[0], 'type': 'upcharge_percent', 'value': Decimal('15.00')},
                    {'package': packages[1], 'type': 'discount_percent', 'value': Decimal('25.00')},
                    {'package': packages[2], 'type': 'upcharge_percent', 'value': Decimal('5.00')},
                    {'package': packages[3], 'type': 'ignore', 'value': Decimal('0.00')},
                ],
                # Large option
                [
                    {'package': packages[0], 'type': 'upcharge_percent', 'value': Decimal('25.00')},
                    {'package': packages[1], 'type': 'upcharge_percent', 'value': Decimal('15.00')},
                    {'package': packages[2], 'type': 'upcharge_percent', 'value': Decimal('15.00')},
                    {'package': packages[3], 'type': 'upcharge_percent', 'value': Decimal('10.00')},
                ],
                # Extra Large option
                [
                    {'package': packages[0], 'type': 'fixed_price', 'value': Decimal('50.00')},
                    {'package': packages[1], 'type': 'upcharge_percent', 'value': Decimal('30.00')},
                    {'package': packages[2], 'type': 'fixed_price', 'value': Decimal('20.00')},
                    {'package': packages[3], 'type': 'upcharge_percent', 'value': Decimal('20.00')},
                ],
            ]

            for i, option in enumerate(options):
                for rule in option_pricing_rules[i]:
                    OptionPricing.objects.create(
                        option=option,
                        package=rule['package'],
                        pricing_type=rule['type'],
                        value=rule['value']
                    )

            self.stdout.write(
                self.style.SUCCESS('Successfully seeded database with sample data')
            )
        else:
            self.stdout.write(
                self.style.WARNING('Service already exists, skipping seed data creation')
            )

