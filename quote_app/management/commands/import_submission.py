import json
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

from accounts.models import Contact, Address
from service_app.models import Service, Package, Question, QuestionOption
from quote_app.models import (
    CustomerSubmission,
    CustomerServiceSelection,
    CustomerPackageQuote,
    CustomerQuestionResponse,
    CustomerOptionResponse,
    CustomerSubQuestionResponse,
    CustomService
)


class Command(BaseCommand):
    help = "Import a customer submission from exported JSON (supports --dry-run)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            required=True,
            help="Path to exported submission JSON file"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate import without saving to DB"
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete existing submission with the same ID before importing"
        )

    def convert_to_decimal(self, value):
        """Convert string to Decimal, handling None and empty strings"""
        if value is None or value == "":
            return Decimal("0.00")
        return Decimal(str(value))
    
    def convert_decimal_fields(self, data_dict, decimal_fields):
        """Convert specified fields to Decimal in a dictionary"""
        for field in decimal_fields:
            if field in data_dict:
                data_dict[field] = self.convert_to_decimal(data_dict[field])
        return data_dict

    def handle(self, *args, **options):
        input_path = options["input"]
        dry_run = options["dry_run"]
        force = options["force"]

        with open(input_path, "r") as f:
            data = json.load(f)

        submission_data = data["submission"].copy()

        # -----------------------------
        # 🔹 LOOKUP EXISTING RELATIONS
        # -----------------------------
        contact_id = submission_data.pop("contact", None)
        address_id = submission_data.pop("address", None)
        submission_id = submission_data.get("id")
        
        contact = None
        address = None

        try:
            if contact_id:
                contact = Contact.objects.get(id=contact_id)
                self.stdout.write(f"✓ Found Contact: {contact} (ID: {contact_id})")
            
            if address_id:
                address = Address.objects.get(id=address_id)
                self.stdout.write(f"✓ Found Address: {address} (ID: {address_id})")
                
        except ObjectDoesNotExist as e:
            self.stderr.write(
                self.style.ERROR(f"Required relation not found: {e}")
            )
            return

        # -----------------------------
        # 🔹 CHECK FOR EXISTING SUBMISSION
        # -----------------------------
        existing_submission = None
        if submission_id:
            try:
                existing_submission = CustomerSubmission.objects.get(id=submission_id)
                if force:
                    self.stdout.write(
                        self.style.WARNING(f"⚠ Found existing submission {submission_id}. Deleting due to --force flag...")
                    )
                    if not dry_run:
                        existing_submission.delete()
                        existing_submission = None
                else:
                    self.stderr.write(
                        self.style.ERROR(
                            f"✗ Submission {submission_id} already exists. Use --force to overwrite."
                        )
                    )
                    return
            except ObjectDoesNotExist:
                pass  # Submission doesn't exist, we can proceed

        # -----------------------------
        # 🔹 DRY-RUN MODE
        # -----------------------------
        if dry_run:
            self.stdout.write(self.style.NOTICE("=== DRY RUN MODE ==="))
            if existing_submission and force:
                self.stdout.write(f"Would delete existing submission: {submission_id}")
            self.stdout.write(f"Submission data: {submission_data}")
            self.stdout.write(f"Mapped Contact: {contact} (ID: {contact.id if contact else 'None'})")
            self.stdout.write(f"Mapped Address: {address} (ID: {address.id if address else 'None'})")
            self.stdout.write(f"\nServices to create: {len(data.get('services', []))}")
            self.stdout.write(f"Packages to create: {len(data.get('packages', []))}")
            self.stdout.write(f"Questions to create: {len(data.get('questions', []))}") 
            self.stdout.write(f"Options to create: {len(data.get('options', []))}")
            self.stdout.write(f"Sub-questions to create: {len(data.get('sub_questions', []))}")
            self.stdout.write(f"Custom services to create: {len(data.get('custom_services', []))}")
            
            # Check if services/packages/questions exist
            for svc in data.get("services", []):
                service_uuid = svc.get("service")
                package_uuid = svc.get("selected_package")
                try:
                    service_obj = Service.objects.get(id=service_uuid)
                    self.stdout.write(f"  ✓ Service found: {service_obj}")
                except ObjectDoesNotExist:
                    self.stderr.write(f"  ✗ Service NOT found: {service_uuid}")
                    
                try:
                    package_obj = Package.objects.get(id=package_uuid)
                    self.stdout.write(f"  ✓ Package found: {package_obj}")
                except ObjectDoesNotExist:
                    self.stderr.write(f"  ✗ Package NOT found: {package_uuid}")
            
            self.stdout.write(self.style.NOTICE("=== END OF DRY RUN ==="))
            return

        # -----------------------------
        # 🔹 ACTUAL IMPORT
        # -----------------------------
        with transaction.atomic():
            # Convert decimal fields in submission_data
            submission_decimal_fields = [
                'total_base_price', 'total_adjustments', 'total_surcharges',
                'custom_service_total', 'final_total'
            ]
            submission_data_copy = submission_data.copy()
            submission_data_copy = self.convert_decimal_fields(submission_data_copy, submission_decimal_fields)
            
            # Create CustomerSubmission (keep original UUID if present)
            submission_id = submission_data_copy.pop("id", None)
            submission = CustomerSubmission(
                id=submission_id,
                contact=contact,
                address=address,
                **submission_data_copy
            )
            submission.save()
            self.stdout.write(f"✓ Created CustomerSubmission: {submission.id}")

            service_selection_map = {}
            question_response_map = {}

            # -----------------------------
            # 🔹 CREATE SERVICE SELECTIONS
            # -----------------------------
            service_decimal_fields = [
                'question_adjustments', 'surcharge_amount',
                'final_base_price', 'final_sqft_price', 'final_total_price'
            ]
            
            for svc_data in data.get("services", []):
                # In the JSON, 'submission' field contains the service_selection UUID
                service_selection_id = svc_data.pop("submission")  # This is actually the service_selection ID
                service_uuid = svc_data.pop("service")
                package_uuid = svc_data.pop("selected_package")
                
                # Convert decimal fields
                svc_data = self.convert_decimal_fields(svc_data, service_decimal_fields)
                
                try:
                    service_obj = Service.objects.get(id=service_uuid)
                    package_obj = Package.objects.get(id=package_uuid)
                    
                    service_selection = CustomerServiceSelection(
                        id=service_selection_id,  # Use the UUID from 'submission' field
                        submission=submission,
                        service=service_obj,
                        selected_package=package_obj,
                        **svc_data
                    )
                    service_selection.save()
                    
                    # Map using the service_selection ID
                    service_selection_map[service_selection_id] = service_selection
                    self.stdout.write(f"  ✓ Created ServiceSelection for: {service_obj.name} (ID: {service_selection_id})")
                    
                except ObjectDoesNotExist as e:
                    self.stderr.write(f"  ✗ Error creating ServiceSelection: {e}")

            # -----------------------------
            # 🔹 CREATE PACKAGE QUOTES
            # -----------------------------
            package_decimal_fields = [
                'base_price', 'sqft_price', 'question_adjustments',
                'surcharge_amount', 'total_price'
            ]
            
            for pkg_data in data.get("packages", []):
                package_quote_id = pkg_data.pop("id", None)
                service_selection_uuid = pkg_data.pop("service_selection")
                package_uuid = pkg_data.pop("package")
                
                # Convert decimal fields
                pkg_data = self.convert_decimal_fields(pkg_data, package_decimal_fields)
                
                service_selection = service_selection_map.get(service_selection_uuid)
                
                if not service_selection:
                    self.stderr.write(f"  ✗ ServiceSelection {service_selection_uuid} not found for package")
                    continue
                
                try:
                    package_obj = Package.objects.get(id=package_uuid)
                    
                    package_quote = CustomerPackageQuote(
                        id=package_quote_id,  # Preserve original ID
                        service_selection=service_selection,
                        package=package_obj,
                        **pkg_data
                    )
                    package_quote.save()
                    self.stdout.write(f"  ✓ Created PackageQuote for: {package_obj.name} (ID: {package_quote_id})")
                    
                except ObjectDoesNotExist as e:
                    self.stderr.write(f"  ✗ Error creating PackageQuote: {e}")

            # -----------------------------
            # 🔹 CREATE QUESTION RESPONSES
            # -----------------------------
            question_decimal_fields = ['price_adjustment']
            
            for q_data in data.get("questions", []):
                question_response_id = q_data.pop("id", None)
                service_selection_uuid = q_data.pop("service_selection")
                question_uuid = q_data.pop("question")
                
                # Convert decimal fields
                q_data = self.convert_decimal_fields(q_data, question_decimal_fields)
                
                service_selection = service_selection_map.get(service_selection_uuid)
                
                if not service_selection:
                    self.stderr.write(f"  ✗ ServiceSelection {service_selection_uuid} not found for question")
                    continue
                
                try:
                    question_obj = Question.objects.get(id=question_uuid)
                    
                    question_response = CustomerQuestionResponse(
                        id=question_response_id,  # Preserve original ID
                        service_selection=service_selection,
                        question=question_obj,
                        **q_data
                    )
                    question_response.save()
                    
                    # Map using the question_response ID
                    question_response_map[question_response_id] = question_response
                    self.stdout.write(f"  ✓ Created QuestionResponse for: {question_obj.text} (ID: {question_response_id})")
                    
                except ObjectDoesNotExist as e:
                    self.stderr.write(f"  ✗ Error creating QuestionResponse: {e}")

            # -----------------------------
            # 🔹 CREATE OPTION RESPONSES
            # -----------------------------
            option_decimal_fields = ['price_adjustment']
            
            for opt_data in data.get("options", []):
                option_response_id = opt_data.pop("id", None)
                question_response_uuid = opt_data.pop("question_response")
                option_uuid = opt_data.pop("option")
                
                # Convert decimal fields
                opt_data = self.convert_decimal_fields(opt_data, option_decimal_fields)
                
                question_response = question_response_map.get(question_response_uuid)
                
                if not question_response:
                    self.stderr.write(f"  ✗ QuestionResponse {question_response_uuid} not found for option")
                    continue
                
                try:
                    option_obj = QuestionOption.objects.get(id=option_uuid)
                    
                    option_response = CustomerOptionResponse(
                        id=option_response_id,  # Preserve original ID
                        question_response=question_response,
                        option=option_obj,
                        **opt_data
                    )
                    option_response.save()
                    self.stdout.write(f"  ✓ Created OptionResponse (ID: {option_response_id})")
                    
                except ObjectDoesNotExist as e:
                    self.stderr.write(f"  ✗ Error creating OptionResponse: {e}")

            # -----------------------------
            # 🔹 CREATE SUB-QUESTION RESPONSES
            # -----------------------------
            for sq_data in data.get("sub_questions", []):
                sub_question_id = sq_data.pop("id", None)
                question_response_uuid = sq_data.pop("question_response")
                
                question_response = question_response_map.get(question_response_uuid)
                
                if not question_response:
                    self.stderr.write(f"  ✗ QuestionResponse {question_response_uuid} not found for sub-question")
                    continue
                
                sub_question_response = CustomerSubQuestionResponse(
                    id=sub_question_id,  # Preserve original ID
                    question_response=question_response,
                    **sq_data
                )
                sub_question_response.save()
                self.stdout.write(f"  ✓ Created SubQuestionResponse (ID: {sub_question_id})")

            # -----------------------------
            # 🔹 CREATE CUSTOM SERVICES
            # -----------------------------
            for cs_data in data.get("custom_services", []):
                cs_id = cs_data.pop("id", None)
                cs_data.pop("purchase", None)  # Remove old reference
                
                custom_service = CustomService(
                    id=cs_id,
                    purchase=submission,
                    **cs_data
                )
                custom_service.save()
                self.stdout.write(f"  ✓ Created CustomService: {custom_service.product_name}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✅ Import completed successfully → Submission ID: {submission.id}"
            )
        )