"""
Management command to import job tracker data from CSV files with bulk operations.

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

# Batch size for bulk operations
BATCH_SIZE = 1000


class Command(BaseCommand):
    help = 'Import job tracker data from CSV files with bulk operations'

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
        parser.add_argument(
            '--batch-size',
            type=int,
            default=BATCH_SIZE,
            help=f'Batch size for bulk operations (default: {BATCH_SIZE})',
        )
        parser.add_argument(
            '--delete-missing',
            action='store_true',
            help='Delete jobs that are not in the CSV file',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dry_run = False
        self.csv_dir = '.'
        self.batch_size = BATCH_SIZE
        self.stats = {
            'users_created': 0,
            'users_matched': 0,
            'contacts_created': 0,
            'contacts_matched': 0,
            'addresses_created': 0,
            'jobs_created': 0,
            'jobs_skipped': 0,
            'jobs_updated': 0,
            'job_items_created': 0,
            'job_items_skipped': 0,
            'job_assignments_created': 0,
            'job_assignments_skipped': 0,
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
        self.batch_size = options['batch_size']
        self.options = options  # Store options for later use
        
        if not os.path.isdir(self.csv_dir):
            raise CommandError(f'CSV directory does not exist: {self.csv_dir}')

        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('Job Tracker Data Import (Bulk Operations)'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN MODE: No database changes will be made'))
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
        
        # Print statistics
        self.print_statistics()

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

    def import_users(self):
        """Import users from users_rows.csv using bulk operations"""
        csv_path = os.path.join(self.csv_dir, 'users_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Users CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[1/8] Importing users...')
        
        users_to_create = []
        existing_emails = set(User.objects.values_list('email', flat=True))
        
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
                    elif email not in existing_emails:
                        # Generate username from email
                        username = email.split('@')[0]
                        base_username = username
                        counter = 1
                        while User.objects.filter(username=username).exists():
                            username = f"{base_username}{counter}"
                            counter += 1
                        
                        if not self.dry_run:
                            user = User(
                                username=username,
                                email=email,
                                first_name=name.split()[0] if name else '',
                                last_name=' '.join(name.split()[1:]) if len(name.split()) > 1 else '',
                                role=role if role in ['manager', 'supervisor', 'worker'] else 'worker',
                                is_active=active,
                            )
                            users_to_create.append(user)
                            existing_emails.add(email)
                        
                        # Store in map (will be None in dry-run)
                        self.user_map[user_id] = user if not self.dry_run else type('User', (), {'email': email})()
                        self.stats['users_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing user {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create users
        if users_to_create and not self.dry_run:
            User.objects.bulk_create(users_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            # Re-fetch to get IDs
            for user in users_to_create:
                if user.email:
                    fetched_user = User.objects.filter(email=user.email).first()
                    if fetched_user:
                        # Update user_map with actual user
                        for csv_id, mapped_user in list(self.user_map.items()):
                            if hasattr(mapped_user, 'email') and mapped_user.email == fetched_user.email:
                                self.user_map[csv_id] = fetched_user
        
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
        
        # Get all services in one query
        all_services = {s.name.lower(): s for s in Service.objects.all()}
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    service_id = row.get('id', '').strip()
                    service_name = row.get('name', '').strip()
                    
                    if not service_id or not service_name:
                        continue
                    
                    # Try to find existing service by name (case-insensitive)
                    service = all_services.get(service_name.lower())
                    
                    if service:
                        self.service_map[service_id] = service
                    else:
                        # Mark as custom service (will use custom_name in JobServiceItem)
                        self.service_map[service_id] = None
                
                except Exception as e:
                    error_msg = f"Error mapping service {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        matched = sum(1 for v in self.service_map.values() if v is not None)
        custom = sum(1 for v in self.service_map.values() if v is None)
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Services: {matched} matched, {custom} marked as custom'
        ))

    def import_contacts_and_addresses(self):
        """Import contacts and addresses from jobs data using bulk operations"""
        csv_path = os.path.join(self.csv_dir, 'jobs_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Jobs CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[3/8] Importing contacts and addresses...')
        
        # Get existing contacts
        existing_contacts_by_ghl = {c.contact_id: c for c in Contact.objects.all() if c.contact_id}
        existing_contacts_by_email = {c.email.lower(): c for c in Contact.objects.filter(email__isnull=False) if c.email}
        existing_contacts_by_phone = {c.phone: c for c in Contact.objects.filter(phone__isnull=False) if c.phone}
        
        contacts_to_create = []
        addresses_data = []  # Store address data separately, will create after contacts are saved
        seen_contacts = set()
        
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
                        contact = existing_contacts_by_ghl.get(ghl_contact_id)
                    
                    if not contact and customer_email:
                        contact = existing_contacts_by_email.get(customer_email.lower())
                    
                    if not contact and customer_phone:
                        contact = existing_contacts_by_phone.get(customer_phone)
                    
                    if contact:
                        self.contact_map[contact_key] = contact
                        self.stats['contacts_matched'] += 1
                    else:
                        # Create new contact
                        if not self.dry_run:
                            # Parse name
                            first_name = customer_name.split()[0] if customer_name else ''
                            last_name = ' '.join(customer_name.split()[1:]) if customer_name and len(customer_name.split()) > 1 else ''
                            
                            contact_id = ghl_contact_id or str(uuid.uuid4())
                            contact = Contact(
                                contact_id=contact_id,
                                first_name=first_name,
                                last_name=last_name,
                                email=customer_email or None,
                                phone=customer_phone or None,
                                location_id='',  # Default, can be updated later
                            )
                            contacts_to_create.append(contact)
                            # Update lookup dicts
                            if ghl_contact_id:
                                existing_contacts_by_ghl[ghl_contact_id] = contact
                            if customer_email:
                                existing_contacts_by_email[customer_email.lower()] = contact
                            if customer_phone:
                                existing_contacts_by_phone[customer_phone] = contact
                        
                        self.contact_map[contact_key] = contact
                        self.stats['contacts_created'] += 1
                    
                    # Store address data for later (after contacts are saved)
                    if customer_address and contact and not self.dry_run:
                        # Check if address already exists (simple check) - only for existing contacts
                        if hasattr(contact, 'pk') and contact.pk:
                            existing_address = Address.objects.filter(
                                contact=contact,
                                street_address__icontains=customer_address[:50]  # Partial match
                            ).first()
                            if existing_address:
                                continue
                        
                        # Parse address (simple parsing)
                        address_parts = customer_address.split(',')
                        street_address = address_parts[0].strip() if address_parts else customer_address
                        city = address_parts[1].strip() if len(address_parts) > 1 else ''
                        state = address_parts[2].strip() if len(address_parts) > 2 else ''
                        postal_code = address_parts[3].strip() if len(address_parts) > 3 else ''
                        
                        # Store address data with contact identifier
                        addresses_data.append({
                            'contact_key': contact_key,
                            'contact_id': contact.contact_id if hasattr(contact, 'contact_id') else None,
                            'address_id': str(uuid.uuid4()),
                            'street_address': street_address,
                            'city': city,
                            'state': state,
                            'postal_code': postal_code,
                        })
                        self.stats['addresses_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing contact: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create contacts first
        if contacts_to_create and not self.dry_run:
            Contact.objects.bulk_create(contacts_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            # Re-fetch contacts to get saved instances
            contact_ids = [c.contact_id for c in contacts_to_create]
            fetched_contacts = {c.contact_id: c for c in Contact.objects.filter(contact_id__in=contact_ids)}
            
            # Update contact_map with saved contacts
            for contact in contacts_to_create:
                fetched = fetched_contacts.get(contact.contact_id)
                if fetched:
                    # Update contact_map
                    for key, mapped_contact in list(self.contact_map.items()):
                        if hasattr(mapped_contact, 'contact_id') and mapped_contact.contact_id == fetched.contact_id:
                            self.contact_map[key] = fetched
        
        # Now create addresses with saved contacts
        addresses_to_create = []
        for addr_data in addresses_data:
            # Get the contact from contact_map (now contains saved contacts)
            contact = self.contact_map.get(addr_data['contact_key'])
            if contact and (hasattr(contact, 'pk') and contact.pk):
                addresses_to_create.append(Address(
                    contact=contact,
                    address_id=addr_data['address_id'],
                    name='Primary',
                    street_address=addr_data['street_address'],
                    city=addr_data['city'],
                    state=addr_data['state'],
                    postal_code=addr_data['postal_code'],
                    order=0,
                ))
        
        # Bulk create addresses
        if addresses_to_create and not self.dry_run:
            Address.objects.bulk_create(addresses_to_create, batch_size=self.batch_size, ignore_conflicts=True)
        
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
            # Get all contacts for lookup
            contacts_by_ghl = {c.contact_id: c for c in Contact.objects.all() if c.contact_id}
            
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
                            contact = contacts_by_ghl.get(ghl_contact_id)
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
        """Import jobs from jobs_rows.csv using bulk operations"""
        csv_path = os.path.join(self.csv_dir, 'jobs_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Jobs CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[5/8] Importing jobs...')
        
        # Get existing job IDs
        existing_job_ids = set(Job.objects.values_list('id', flat=True))
        
        jobs_to_create = []
        jobs_to_update = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            total = 0
            
            for row in reader:
                total += 1
                try:
                    job_id = row.get('id', '').strip()
                    if not job_id:
                        continue
                    
                    try:
                        job_uuid = uuid.UUID(job_id)
                    except ValueError:
                        continue
                    
                    # Check if job already exists
                    if job_id in existing_job_ids:
                        self.stats['jobs_skipped'] += 1
                        # Still add to job_map for linking
                        job = Job.objects.filter(id=job_id).first()
                        if job:
                            self.job_map[job_id] = job
                        continue
                    
                    # Parse fields
                    title = row.get('title', '').strip() or None
                    description = row.get('description', '').strip() or None
                    # Parse status - handle None/empty values properly
                    status_str = (row.get('status') or '').strip()
                    if not status_str:
                        status_str = 'pending'
                    status = self.map_status(status_str)
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
                            if timezone.is_naive(scheduled_at):
                                scheduled_at = timezone.make_aware(scheduled_at)
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
                    
                    # Create job object
                    if not self.dry_run:
                        job = Job(
                            id=job_uuid,
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
                        jobs_to_create.append(job)
                        if submission:
                            self.stats['submissions_linked'] += 1
                    else:
                        job = type('Job', (), {'id': job_id})()  # Mock object for dry-run
                    
                    self.job_map[job_id] = job
                    self.stats['jobs_created'] += 1
                    
                    if total % 1000 == 0:
                        self.stdout.write(f'  Processed {total} jobs...')
                
                except Exception as e:
                    error_msg = f"Error importing job {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create/update jobs
        if jobs_to_create and not self.dry_run:
            # Check which jobs already exist
            job_ids_to_check = [j.id for j in jobs_to_create]
            existing_job_ids = set(Job.objects.filter(id__in=job_ids_to_check).values_list('id', flat=True))
            
            # Separate new jobs from existing ones
            new_jobs = [j for j in jobs_to_create if j.id not in existing_job_ids]
            existing_jobs_dict = {j.id: j for j in jobs_to_create if j.id in existing_job_ids}
            
            # Create new jobs
            # Note: With ForeignKey, multiple jobs can share the same submission (no OneToOne constraint)
            if new_jobs:
                try:
                    Job.objects.bulk_create(new_jobs, batch_size=self.batch_size, ignore_conflicts=True)
                    # Verify which jobs were actually created (bulk_create with ignore_conflicts may skip some)
                    created_job_ids = set(Job.objects.filter(id__in=[j.id for j in new_jobs]).values_list('id', flat=True))
                    actually_created = [j for j in new_jobs if j.id in created_job_ids]
                    failed_to_create = [j for j in new_jobs if j.id not in created_job_ids]
                    
                    if failed_to_create:
                        # Try to create failed ones individually to get better error messages
                        retry_created = []
                        for job in failed_to_create[:]:
                            try:
                                job.save()
                                retry_created.append(job)
                                failed_to_create.remove(job)
                                actually_created.append(job)
                            except Exception as e:
                                error_msg = f"Failed to create job {job.id}: {str(e)}"
                                self.stats['errors'].append(error_msg)
                                if len(self.stats['errors']) <= 20:
                                    self.stdout.write(self.style.ERROR(error_msg))
                                if str(job.id) in self.job_map:
                                    del self.job_map[str(job.id)]
                        
                        if retry_created:
                            self.stdout.write(self.style.SUCCESS(f'  ✓ Retried and created {len(retry_created)} jobs'))
                    
                    self.stdout.write(self.style.SUCCESS(f'  ✓ Created {len(actually_created)} new jobs'))
                    if failed_to_create:
                        self.stdout.write(self.style.WARNING(f'  ⚠ {len(failed_to_create)} jobs failed to create (check errors above)'))
                        
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error in bulk_create: {str(e)}"))
                    # Try to create individually to see which ones fail
                    created_count = 0
                    failed_jobs = []
                    for job in new_jobs:
                        try:
                            job.save()
                            created_count += 1
                        except Exception as e2:
                            failed_jobs.append((job.id, str(e2)))
                            error_msg = f"Failed to create job {job.id}: {str(e2)}"
                            self.stats['errors'].append(error_msg)
                            if len(self.stats['errors']) <= 20:
                                self.stdout.write(self.style.ERROR(error_msg))
                            if str(job.id) in self.job_map:
                                del self.job_map[str(job.id)]
                    
                    if created_count > 0:
                        self.stdout.write(self.style.SUCCESS(f'  ✓ Created {created_count} new jobs'))
                    if failed_jobs:
                        self.stdout.write(self.style.WARNING(f'  ⚠ {len(failed_jobs)} jobs failed to create'))
            
            # Update existing jobs with correct status and other fields from CSV
            if existing_jobs_dict:
                # Re-fetch existing jobs from database
                existing_job_objs = {j.id: j for j in Job.objects.filter(id__in=existing_jobs_dict.keys())}
                jobs_to_update = []
                
                for job_id, csv_job in existing_jobs_dict.items():
                    existing = existing_job_objs.get(job_id)
                    if existing:
                        # Update all fields from CSV, especially status
                        existing.status = csv_job.status  # This is the key fix - update status
                        existing.title = csv_job.title
                        existing.description = csv_job.description
                        existing.priority = csv_job.priority
                        existing.duration_hours = csv_job.duration_hours
                        existing.scheduled_at = csv_job.scheduled_at
                        existing.total_price = csv_job.total_price
                        existing.customer_name = csv_job.customer_name
                        existing.customer_phone = csv_job.customer_phone
                        existing.customer_email = csv_job.customer_email
                        existing.customer_address = csv_job.customer_address
                        existing.ghl_contact_id = csv_job.ghl_contact_id
                        existing.job_type = csv_job.job_type
                        existing.quoted_by = csv_job.quoted_by
                        # With ForeignKey, multiple jobs can share the same submission
                        existing.submission = csv_job.submission
                        existing.notes = csv_job.notes
                        existing.created_at = csv_job.created_at
                        jobs_to_update.append(existing)
                
                if jobs_to_update:
                    Job.objects.bulk_update(
                        jobs_to_update,
                        ['status', 'title', 'description', 'priority', 'duration_hours', 
                         'scheduled_at', 'total_price', 'customer_name', 'customer_phone', 
                         'customer_email', 'customer_address', 'ghl_contact_id', 'job_type',
                         'quoted_by', 'submission', 'notes', 'created_at'],
                        batch_size=self.batch_size
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ Updated {len(jobs_to_update)} existing jobs with correct status'
                    ))
            
            # Re-fetch all jobs to get full objects and verify which ones were actually created
            job_ids = [str(j.id) for j in jobs_to_create]
            fetched_jobs = {str(j.id): j for j in Job.objects.filter(id__in=job_ids)}
            
            # Track which jobs failed to be created
            failed_job_ids = []
            
            # Update job_map only with jobs that actually exist in database
            for job in jobs_to_create:
                fetched = fetched_jobs.get(str(job.id))
                if fetched:
                    self.job_map[str(job.id)] = fetched
                else:
                    # Job wasn't created (maybe conflict or error), remove from map
                    failed_job_ids.append(str(job.id))
                    if str(job.id) in self.job_map:
                        del self.job_map[str(job.id)]
            
            # Report any jobs that failed to be created
            if failed_job_ids:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠ {len(failed_job_ids)} jobs from CSV were not created in database'
                ))
                if len(failed_job_ids) <= 10:
                    for job_id in failed_job_ids[:10]:
                        self.stdout.write(self.style.WARNING(f'    - Job ID: {job_id}'))
                else:
                    self.stdout.write(self.style.WARNING(f'    (showing first 10 of {len(failed_job_ids)} failed jobs)'))
        
        # Delete jobs not in CSV if --delete-missing flag is set
        if self.options.get('delete_missing') and not self.dry_run:
            self.stdout.write('\n[Cleanup] Deleting jobs not in CSV...')
            csv_job_ids = set(j.id for j in jobs_to_create)
            jobs_to_delete = Job.objects.exclude(id__in=csv_job_ids)
            delete_count = jobs_to_delete.count()
            
            if delete_count > 0:
                # Delete related records first to avoid foreign key constraints
                job_ids_to_delete = list(jobs_to_delete.values_list('id', flat=True))
                
                # Delete job service items
                JobServiceItem.objects.filter(job_id__in=job_ids_to_delete).delete()
                
                # Delete job assignments
                JobAssignment.objects.filter(job_id__in=job_ids_to_delete).delete()
                
                # Delete job occurrences
                JobOccurrence.objects.filter(job_id__in=job_ids_to_delete).delete()
                
                # Now delete the jobs
                jobs_to_delete.delete()
                
                self.stdout.write(self.style.SUCCESS(
                    f'  ✓ Deleted {delete_count} jobs not in CSV'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    '  ✓ No jobs to delete (all jobs are in CSV)'
                ))
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Jobs: {self.stats["jobs_created"]} created, {self.stats["jobs_skipped"]} skipped'
        ))
        if self.stats['submissions_linked'] > 0:
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Submissions linked: {self.stats["submissions_linked"]}'
            ))

    def import_job_service_items(self):
        """Import job service items from job_services_rows.csv using bulk operations"""
        csv_path = os.path.join(self.csv_dir, 'job_services_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job services CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[6/8] Importing job service items...')
        
        # Get existing item IDs to prevent duplicates
        existing_item_ids = set(JobServiceItem.objects.values_list('id', flat=True))
        
        items_to_create = []
        
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
                    
                    # Check for duplicates
                    if item_id in existing_item_ids:
                        self.stats['job_items_skipped'] += 1
                        continue
                    
                    try:
                        item_uuid = uuid.UUID(item_id)
                    except ValueError:
                        continue
                    
                    job = self.job_map.get(job_id)
                    if not job:
                        continue
                    
                    # Verify job is actually saved (has pk) - skip if it's a mock or unsaved object
                    if not hasattr(job, 'pk') or not job.pk:
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
                        items_to_create.append(JobServiceItem(
                            id=item_uuid,
                            job=job,
                            service=service,
                            custom_name=custom_name,
                            price=price,
                            duration_hours=duration,
                        ))
                    
                    self.stats['job_items_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job service item {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create job service items
        if items_to_create and not self.dry_run:
            JobServiceItem.objects.bulk_create(items_to_create, batch_size=self.batch_size, ignore_conflicts=True)
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job service items: {self.stats["job_items_created"]} created, {self.stats["job_items_skipped"]} skipped'
        ))

    def import_job_assignments(self):
        """Import job assignments from job_assignments_rows.csv using bulk operations"""
        csv_path = os.path.join(self.csv_dir, 'job_assignments_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job assignments CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[7/8] Importing job assignments...')
        
        # Get existing assignment IDs to prevent duplicates
        existing_assignment_ids = set(JobAssignment.objects.values_list('id', flat=True))
        
        assignments_to_create = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    assignment_id = row.get('id', '').strip()
                    job_id = row.get('job_id', '').strip()
                    user_id = row.get('user_id', '').strip()
                    
                    if not assignment_id or not job_id or not user_id:
                        continue
                    
                    # Check for duplicates
                    if assignment_id in existing_assignment_ids:
                        self.stats['job_assignments_skipped'] += 1
                        continue
                    
                    try:
                        assignment_uuid = uuid.UUID(assignment_id)
                    except ValueError:
                        continue
                    
                    job = self.job_map.get(job_id)
                    user = self.user_map.get(user_id)
                    
                    if not job or not user:
                        continue
                    
                    # Verify job is actually saved (has pk) - skip if it's a mock or unsaved object
                    if not hasattr(job, 'pk') or not job.pk:
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
                        assignments_to_create.append(JobAssignment(
                            id=assignment_uuid,
                            job=job,
                            user=user,
                            created_at=assigned_at,
                        ))
                    
                    self.stats['job_assignments_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job assignment {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create job assignments
        if assignments_to_create and not self.dry_run:
            JobAssignment.objects.bulk_create(assignments_to_create, batch_size=self.batch_size, ignore_conflicts=True)
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job assignments: {self.stats["job_assignments_created"]} created, {self.stats["job_assignments_skipped"]} skipped'
        ))

    def import_job_schedules(self):
        """Import job schedules (recurring jobs) from job_schedules_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'job_schedules_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Job schedules CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[8/8] Importing job schedules...')
        
        jobs_to_update = []
        
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
                    if not job or isinstance(job, type('Job', (), {})):  # Skip mock objects in dry-run
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
                        
                        jobs_to_update.append(job)
                    
                    self.stats['job_schedules_processed'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing job schedule {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk update jobs
        if jobs_to_update and not self.dry_run:
            Job.objects.bulk_update(
                jobs_to_update,
                ['repeat_unit', 'repeat_every', 'job_type', 'scheduled_at'],
                batch_size=self.batch_size
            )
        
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
        self.stdout.write(f'  Service items skipped: {self.stats["job_items_skipped"]}')
        self.stdout.write(f'  Assignments created: {self.stats["job_assignments_created"]}')
        self.stdout.write(f'  Assignments skipped: {self.stats["job_assignments_skipped"]}')
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
