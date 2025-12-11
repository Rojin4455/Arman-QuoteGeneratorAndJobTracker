"""
Management command to import job tracker data from CSV files.

Usage:
    python manage.py import_jobtracker_data --dry-run
    python manage.py import_jobtracker_data --csv-dir /path/to/csv/files
"""

import csv
import os
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model

from service_app.models import Service, User
from accounts.models import Contact, Address
from quote_app.models import CustomerSubmission
from jobtracker_app.models import Job, JobServiceItem, JobAssignment, JobOccurrence

User = get_user_model()


class Command(BaseCommand):
    help = 'Import job tracker data from CSV files'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-dir',
            type=str,
            default='.',
            help='Directory containing CSV files (default: current directory)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run in dry-run mode (no database changes)',
        )
        parser.add_argument(
            '--skip-users',
            action='store_true',
            help='Skip importing users',
        )
        parser.add_argument(
            '--skip-services',
            action='store_true',
            help='Skip importing services mapping',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dry_run = False
        self.csv_dir = '.'
        self.stats = {
            'users_created': 0,
            'users_matched': 0,
            'contacts_created': 0,
            'contacts_matched': 0,
            'addresses_created': 0,
            'jobs_created': 0,
            'jobs_skipped': 0,
            'job_items_created': 0,
            'job_assignments_created': 0,
            'job_schedules_processed': 0,
            'submissions_linked': 0,
            'errors': [],
        }
        
        # Mapping dictionaries
        self.service_map = {}  # CSV service_id -> Service object or None (custom)
        self.user_map = {}  # CSV user_id -> User object
        self.contact_map = {}  # CSV contact identifier -> Contact object
        self.job_map = {}  # CSV job_id -> Job object
        self.submission_map = {}  # For linking jobs to submissions

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.csv_dir = options['csv_dir']
        
        if not os.path.isdir(self.csv_dir):
            raise CommandError(f'CSV directory does not exist: {self.csv_dir}')

        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('Job Tracker Data Import'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN MODE: No database changes will be made'))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY-RUN mode - no database changes'))
        else:
            self.stdout.write(self.style.SUCCESS('Running in IMPORT mode - database changes will be saved'))
        
        try:
            if not self.dry_run:
                # Real import: wrap everything in a transaction
                with transaction.atomic():
                    self._run_import_steps(options)
            else:
                # Dry-run: simulate without transaction
                self._run_import_steps(options)
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during import: {str(e)}'))
            if not self.dry_run:
                raise
    
    def _run_import_steps(self, options):
        """Run all import steps"""
        # Step 1: Import users
        if not options['skip_users']:
            self.import_users()
        else:
            self.stdout.write(self.style.WARNING('Skipping user import'))
        
        # Step 2: Map services
        if not options['skip_services']:
            self.map_services()
        else:
            self.stdout.write(self.style.WARNING('Skipping service mapping'))
        
        # Step 3: Import contacts and addresses
        self.import_contacts_and_addresses()
        
        # Step 4: Build submission map for linking
        self.build_submission_map()
        
        # Step 5: Import jobs
        self.import_jobs()
        
        # Step 6: Import job service items
        self.import_job_service_items()
        
        # Step 7: Import job assignments
        self.import_job_assignments()
        
        # Step 8: Import job schedules (recurring jobs)
        self.import_job_schedules()
        
        # Print statistics
        self.print_statistics()

    def import_users(self):
        """Import users from users_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'users_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Users CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[1/8] Importing users...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    user_id = row.get('id', '').strip()
                    email = row.get('email', '').strip()
                    name = row.get('name', '').strip()
                    phone = row.get('phone', '').strip()
                    role = row.get('role', 'worker').strip()
                    active = row.get('active', 'true').lower() == 'true'
                    
                    if not user_id or not email:
                        continue
                    
                    # Try to find existing user by email
                    user = User.objects.filter(email=email).first()
                    
                    if user:
                        self.user_map[user_id] = user
                        self.stats['users_matched'] += 1
                        if not self.dry_run:
                            # Update role if needed
                            if role in ['manager', 'supervisor', 'worker']:
                                user.role = role
                                user.save()
                    else:
                        # Create new user
                        if not self.dry_run:
                            # Generate username from email
                            username = email.split('@')[0]
                            # Ensure unique username
                            base_username = username
                            counter = 1
                            while User.objects.filter(username=username).exists():
                                username = f"{base_username}{counter}"
                                counter += 1
                            
                            user = User.objects.create(
                                username=username,
                                email=email,
                                first_name=name.split()[0] if name else '',
                                last_name=' '.join(name.split()[1:]) if len(name.split()) > 1 else '',
                                role=role if role in ['manager', 'supervisor', 'worker'] else 'worker',
                                is_active=active,
                            )
                            if not user.is_active:
                                user.is_active = active
                                user.save()
                        
                        self.user_map[user_id] = user
                        self.stats['users_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing user {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Users: {self.stats["users_created"]} created, {self.stats["users_matched"]} matched'
        ))

    def map_services(self):
        """Map services from CSV to existing Service objects or mark as custom"""
        csv_path = os.path.join(self.csv_dir, 'services_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Services CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[2/8] Mapping services...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    service_id = row.get('id', '').strip()
                    service_name = row.get('name', '').strip()
                    
                    if not service_id or not service_name:
                        continue
                    
                    # Try to find existing service by name (case-insensitive)
                    service = Service.objects.filter(name__iexact=service_name).first()
                    
                    if service:
                        self.service_map[service_id] = service
                    else:
                        # Mark as custom service (will use custom_name in JobServiceItem)
                        self.service_map[service_id] = None
                
                except Exception as e:
                    error_msg = f"Error mapping service {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    self.stdout.write(self.style.ERROR(error_msg))
        
        matched = sum(1 for v in self.service_map.values() if v is not None)
        custom = sum(1 for v in self.service_map.values() if v is None)
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Services: {matched} matched, {custom} marked as custom'
        ))

    def import_contacts_and_addresses(self):
        """Import contacts and addresses from jobs data"""
        csv_path = os.path.join(self.csv_dir, 'jobs_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Jobs CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[3/8] Importing contacts and addresses...')
        
        seen_contacts = set()  # Track contacts we've already processed
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    customer_email = row.get('customer_email', '').strip()
                    customer_phone = row.get('customer_phone', '').strip()
                    customer_name = row.get('customer_name', '').strip()
                    customer_address = row.get('customer_address', '').strip()
                    ghl_contact_id = row.get('ghl_contact_id', '').strip()
                    
                    # Create contact identifier
                    contact_key = (customer_email or customer_phone or customer_name).lower()
                    if not contact_key or contact_key in seen_contacts:
                        continue
                    
                    seen_contacts.add(contact_key)
                    
                    # Try to find existing contact
                    contact = None
                    if ghl_contact_id:
                        contact = Contact.objects.filter(contact_id=ghl_contact_id).first()
                    
                    if not contact and customer_email:
                        contact = Contact.objects.filter(email=customer_email).first()
                    
                    if not contact and customer_phone:
                        contact = Contact.objects.filter(phone=customer_phone).first()
                    
                    if contact:
                        self.contact_map[contact_key] = contact
                        self.stats['contacts_matched'] += 1
                    else:
                        # Create new contact
                        if not self.dry_run:
                            # Parse name
                            first_name = customer_name.split()[0] if customer_name else ''
                            last_name = ' '.join(customer_name.split()[1:]) if customer_name and len(customer_name.split()) > 1 else ''
                            
                            contact = Contact.objects.create(
                                contact_id=ghl_contact_id or str(uuid.uuid4()),
                                first_name=first_name,
                                last_name=last_name,
                                email=customer_email or None,
                                phone=customer_phone or None,
                                location_id='',  # Default, can be updated later
                            )
                        
                        self.contact_map[contact_key] = contact
                        self.stats['contacts_created'] += 1
                    
                    # Create address if provided
                    if customer_address and contact and not self.dry_run:
                        # Check if address already exists
                        existing_address = Address.objects.filter(
                            contact=contact,
                            street_address__icontains=customer_address[:50]  # Partial match
                        ).first()
                        
                        if not existing_address:
                            # Parse address (simple parsing)
                            address_parts = customer_address.split(',')
                            street_address = address_parts[0].strip() if address_parts else customer_address
                            city = address_parts[1].strip() if len(address_parts) > 1 else ''
                            state = address_parts[2].strip() if len(address_parts) > 2 else ''
                            postal_code = address_parts[3].strip() if len(address_parts) > 3 else ''
                            
                            Address.objects.create(
                                contact=contact,
                                address_id=str(uuid.uuid4()),
                                name='Primary',
                                street_address=street_address,
                                city=city,
                                state=state,
                                postal_code=postal_code,
                                order=0,
                            )
                            self.stats['addresses_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing contact: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:  # Limit error output
                        self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Contacts: {self.stats["contacts_created"]} created, {self.stats["contacts_matched"]} matched'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Addresses: {self.stats["addresses_created"]} created'
        ))

    def build_submission_map(self):
        """Build a map to link jobs to submissions"""
        self.stdout.write('\n[4/8] Building submission map...')
        
        # Try to load accepted quotes CSV
        csv_path = os.path.join(self.csv_dir, 'accepted_quotes_rows.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        customer_email = row.get('customer_email', '').strip()
                        customer_phone = row.get('customer_phone', '').strip()
                        ghl_contact_id = row.get('ghl_contact_id', '').strip()
                        created_at = row.get('created_at', '').strip()
                        
                        # Try to find matching submission
                        submission = None
                        if ghl_contact_id:
                            contact = Contact.objects.filter(contact_id=ghl_contact_id).first()
                            if contact:
                                # Find submission by contact and date
                                if created_at:
                                    try:
                                        created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                        submission = CustomerSubmission.objects.filter(
                                            contact=contact,
                                            created_at__date=created_dt.date()
                                        ).first()
                                    except:
                                        submission = CustomerSubmission.objects.filter(
                                            contact=contact
                                        ).order_by('-created_at').first()
                                else:
                                    submission = CustomerSubmission.objects.filter(
                                        contact=contact
                                    ).order_by('-created_at').first()
                        
                        if submission:
                            # Create key for matching
                            key = (customer_email or customer_phone or '').lower()
                            if key:
                                self.submission_map[key] = submission
                    
                    except Exception as e:
                        continue
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Submission map: {len(self.submission_map)} entries'
        ))

    def import_jobs(self):
        """Import jobs from jobs_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'jobs_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Jobs CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[5/8] Importing jobs...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            total = 0
            
            for row in reader:
                total += 1
                try:
                    job_id = row.get('id', '').strip()
                    if not job_id:
                        continue
                    
                    # Check if job already exists
                    if Job.objects.filter(id=job_id).exists():
                        self.stats['jobs_skipped'] += 1
                        continue
                    
                    # Parse fields
                    title = row.get('title', '').strip() or None
                    description = row.get('description', '').strip() or None
                    status = self.map_status(row.get('status', 'pending').strip())
                    priority = self.map_priority(row.get('priority', '1').strip())
                    
                    # Parse duration
                    try:
                        duration_hours = Decimal(row.get('estimated_duration', '0') or '0')
                    except (InvalidOperation, ValueError):
                        duration_hours = Decimal('0.00')
                    
                    # Parse price
                    try:
                        total_price = Decimal(row.get('price', '0') or '0')
                    except (InvalidOperation, ValueError):
                        total_price = Decimal('0.00')
                    
                    # Parse dates
                    scheduled_at = None
                    if row.get('scheduled_date'):
                        try:
                            scheduled_at = datetime.fromisoformat(
                                row.get('scheduled_date').replace('Z', '+00:00')
                            )
                        except:
                            try:
                                scheduled_at = datetime.strptime(
                                    row.get('scheduled_date'), '%Y-%m-%d %H:%M:%S%z'
                                )
                            except:
                                pass
                    
                    # Customer info
                    customer_name = row.get('customer_name', '').strip() or None
                    customer_phone = row.get('customer_phone', '').strip() or None
                    customer_email = row.get('customer_email', '').strip() or None
                    customer_address = row.get('customer_address', '').strip() or None
                    ghl_contact_id = row.get('ghl_contact_id', '').strip() or None
                    
                    # Job type
                    is_recurring = row.get('is_recurring', 'false').lower() == 'true'
                    job_type = 'recurring' if is_recurring else 'one_time'
                    
                    # Find contact
                    contact = None
                    contact_key = (customer_email or customer_phone or customer_name or '').lower()
                    if contact_key:
                        contact = self.contact_map.get(contact_key)
                    
                    if not contact and ghl_contact_id:
                        contact = Contact.objects.filter(contact_id=ghl_contact_id).first()
                    
                    # Find submission
                    submission = None
                    if contact_key:
                        submission = self.submission_map.get(contact_key)
                    
                    # Find quoted_by user
                    quoted_by = None
                    quoted_by_id = row.get('quoted_by', '').strip()
                    if quoted_by_id:
                        quoted_by = self.user_map.get(quoted_by_id)
                    
                    # Parse created_at
                    created_at = timezone.now()
                    if row.get('created_at'):
                        try:
                            created_at = datetime.fromisoformat(
                                row.get('created_at').replace('Z', '+00:00')
                            )
                            if timezone.is_naive(created_at):
                                created_at = timezone.make_aware(created_at)
                        except:
                            pass
                    
                    # Create job
                    if not self.dry_run:
                        job = Job.objects.create(
                            id=job_id,
                            title=title,
                            description=description,
                            status=status,
                            priority=priority,
                            duration_hours=duration_hours,
                            scheduled_at=scheduled_at,
                            total_price=total_price,
                            customer_name=customer_name,
                            customer_phone=customer_phone,
                            customer_email=customer_email,
                            customer_address=customer_address,
                            ghl_contact_id=ghl_contact_id,
                            job_type=job_type,
                            quoted_by=quoted_by,
                            submission=submission,
                            notes=row.get('notes', '').strip() or None,
                            created_at=created_at,
                        )
                        if submission:
                            self.stats['submissions_linked'] += 1
                    else:
                        job = type('Job', (), {'id': job_id})()  # Mock object for dry-run
                    
                    self.job_map[job_id] = job
                    self.stats['jobs_created'] += 1
                    
                    if total % 100 == 0:
                        self.stdout.write(f'  Processed {total} jobs...')
                
                except Exception as e:
                    error_msg = f"Error importing job {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Jobs: {self.stats["jobs_created"]} created, {self.stats["jobs_skipped"]} skipped'
        ))
        if self.stats['submissions_linked'] > 0:
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Submissions linked: {self.stats["submissions_linked"]}'
            ))

    def import_job_service_items(self):
        """Import job service items from job_services_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'job_services_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job services CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[6/8] Importing job service items...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    item_id = row.get('id', '').strip()
                    job_id = row.get('job_id', '').strip()
                    service_id = row.get('service_id', '').strip()
                    service_name = row.get('service_name', '').strip()
                    
                    if not item_id or not job_id:
                        continue
                    
                    job = self.job_map.get(job_id)
                    if not job:
                        continue
                    
                    # Get service (may be None for custom services)
                    service = self.service_map.get(service_id)
                    custom_name = None
                    if service is None:
                        # This is a custom service
                        custom_name = service_name or 'Custom Service'
                    
                    # Parse price and duration
                    try:
                        price = Decimal(row.get('price', '0') or '0')
                    except (InvalidOperation, ValueError):
                        price = Decimal('0.00')
                    
                    try:
                        duration = Decimal(row.get('duration', '0') or '0')
                    except (InvalidOperation, ValueError):
                        duration = Decimal('0.00')
                    
                    if not self.dry_run:
                        JobServiceItem.objects.create(
                            id=item_id,
                            job=job,
                            service=service,
                            custom_name=custom_name,
                            price=price,
                            duration_hours=duration,
                        )
                    
                    self.stats['job_items_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job service item {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job service items: {self.stats["job_items_created"]} created'
        ))

    def import_job_assignments(self):
        """Import job assignments from job_assignments_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'job_assignments_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job assignments CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[7/8] Importing job assignments...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    assignment_id = row.get('id', '').strip()
                    job_id = row.get('job_id', '').strip()
                    user_id = row.get('user_id', '').strip()
                    
                    if not assignment_id or not job_id or not user_id:
                        continue
                    
                    job = self.job_map.get(job_id)
                    user = self.user_map.get(user_id)
                    
                    if not job or not user:
                        continue
                    
                    # Parse assigned_at
                    assigned_at = timezone.now()
                    if row.get('assigned_at'):
                        try:
                            assigned_at = datetime.fromisoformat(
                                row.get('assigned_at').replace('Z', '+00:00')
                            )
                            if timezone.is_naive(assigned_at):
                                assigned_at = timezone.make_aware(assigned_at)
                        except:
                            pass
                    
                    if not self.dry_run:
                        JobAssignment.objects.create(
                            id=assignment_id,
                            job=job,
                            user=user,
                            created_at=assigned_at,
                        )
                    
                    self.stats['job_assignments_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job assignment {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job assignments: {self.stats["job_assignments_created"]} created'
        ))

    def import_job_schedules(self):
        """Import job schedules (recurring jobs) from job_schedules_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'job_schedules_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job schedules CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[8/8] Importing job schedules...')
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    schedule_id = row.get('id', '').strip()
                    job_id = row.get('job_id', '').strip()
                    frequency = row.get('frequency', '').strip()
                    interval_value = row.get('interval_value', '1').strip()
                    next_due_date = row.get('next_due_date', '').strip()
                    
                    if not schedule_id or not job_id:
                        continue
                    
                    job = self.job_map.get(job_id)
                    if not job:
                        continue
                    
                    # Map frequency to repeat_unit
                    repeat_unit = self.map_frequency(frequency)
                    if not repeat_unit:
                        continue
                    
                    # Parse interval_value
                    try:
                        repeat_every = int(interval_value) if interval_value else 1
                    except (ValueError, TypeError):
                        repeat_every = 1
                    
                    # Update job with recurring info
                    if not self.dry_run:
                        job.repeat_unit = repeat_unit
                        job.repeat_every = repeat_every
                        job.job_type = 'recurring'
                        
                        # Parse next_due_date for scheduled_at if not set
                        if next_due_date and not job.scheduled_at:
                            try:
                                job.scheduled_at = datetime.fromisoformat(
                                    next_due_date.replace('Z', '+00:00')
                                )
                                if timezone.is_naive(job.scheduled_at):
                                    job.scheduled_at = timezone.make_aware(job.scheduled_at)
                            except:
                                pass
                        
                        job.save()
                    
                    self.stats['job_schedules_processed'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job schedule {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job schedules: {self.stats["job_schedules_processed"]} processed'
        ))

    def map_status(self, old_status):
        """Map old status to new status choices"""
        status_map = {
            'pending': 'pending',
            'confirmed': 'confirmed',
            'in_progress': 'in_progress',
            'completed': 'completed',
            'cancelled': 'cancelled',
            'to_convert': 'to_convert',
            'service_due': 'service_due',
            'on_the_way': 'on_the_way',
        }
        return status_map.get(old_status.lower(), 'pending')

    def map_priority(self, old_priority):
        """Map old priority to new priority choices"""
        priority_map = {
            '1': 'low',
            '2': 'medium',
            '3': 'high',
        }
        return priority_map.get(str(old_priority), 'low')

    def map_frequency(self, frequency):
        """Map frequency string to repeat_unit"""
        frequency_map = {
            'daily': 'day',
            'weekly': 'week',
            'monthly': 'month',
            'quarterly': 'quarter',
            'semi_annually': 'semi_annual',
            'semi-annually': 'semi_annual',
            'yearly': 'year',
            'year': 'year',
        }
        return frequency_map.get(frequency.lower(), None)

    def print_statistics(self):
        """Print import statistics"""
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('Import Statistics'))
        self.stdout.write('=' * 80)
        
        self.stdout.write(f'Users:')
        self.stdout.write(f'  Created: {self.stats["users_created"]}')
        self.stdout.write(f'  Matched: {self.stats["users_matched"]}')
        
        self.stdout.write(f'\nContacts:')
        self.stdout.write(f'  Created: {self.stats["contacts_created"]}')
        self.stdout.write(f'  Matched: {self.stats["contacts_matched"]}')
        self.stdout.write(f'  Addresses created: {self.stats["addresses_created"]}')
        
        self.stdout.write(f'\nJobs:')
        self.stdout.write(f'  Created: {self.stats["jobs_created"]}')
        self.stdout.write(f'  Skipped (already exist): {self.stats["jobs_skipped"]}')
        self.stdout.write(f'  Submissions linked: {self.stats["submissions_linked"]}')
        
        self.stdout.write(f'\nJob Items:')
        self.stdout.write(f'  Service items created: {self.stats["job_items_created"]}')
        self.stdout.write(f'  Assignments created: {self.stats["job_assignments_created"]}')
        self.stdout.write(f'  Schedules processed: {self.stats["job_schedules_processed"]}')
        
        if self.stats['errors']:
            self.stdout.write(f'\n{self.style.ERROR("Errors:")}')
            for i, error in enumerate(self.stats['errors'][:20], 1):
                self.stdout.write(self.style.ERROR(f'  {i}. {error}'))
            if len(self.stats['errors']) > 20:
                self.stdout.write(self.style.ERROR(
                    f'  ... and {len(self.stats["errors"]) - 20} more errors'
                ))
        
        self.stdout.write('\n' + '=' * 80)
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: No data was actually imported'))
        else:
            self.stdout.write(self.style.SUCCESS('Import completed!'))

