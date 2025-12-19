"""
Management command to import payroll data from CSV files with bulk operations.

Usage:
    python manage.py import_payroll_data --dry-run
    python manage.py import_payroll_data --csv-dir /path/to/csv/files
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

from service_app.models import User
from jobtracker_app.models import Job
from payroll_app.models import (
    EmployeeProfile, CollaborationRate, TimeEntry, Payout, PayrollSettings
)

User = get_user_model()

# Batch size for bulk operations
BATCH_SIZE = 1000


class Command(BaseCommand):
    help = 'Import payroll data from CSV files with bulk operations'

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
            '--batch-size',
            type=int,
            default=BATCH_SIZE,
            help=f'Batch size for bulk operations (default: {BATCH_SIZE})',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dry_run = False
        self.csv_dir = '.'
        self.batch_size = BATCH_SIZE
        self.stats = {
            'employee_profiles_created': 0,
            'employee_profiles_updated': 0,
            'collaboration_rates_created': 0,
            'collaboration_rates_skipped': 0,
            'time_entries_created': 0,
            'time_entries_skipped': 0,
            'payouts_created': 0,
            'payouts_skipped': 0,
            'payouts_auto_created': 0,  # Auto-created payouts for time entries
            'settings_updated': 0,
            'errors': [],
        }
        
        # Mapping dictionaries
        self.user_map = {}  # CSV employee_id -> User object
        self.job_map = {}  # CSV job_id -> Job object (for payouts)
        self.time_entry_map = {}  # (employee_id, check_in_time, check_out_time) -> TimeEntry object

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.csv_dir = options['csv_dir']
        self.batch_size = options['batch_size']
        
        if not os.path.isdir(self.csv_dir):
            raise CommandError(f'CSV directory does not exist: {self.csv_dir}')

        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('Payroll Data Import (Bulk Operations)'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN MODE: No database changes will be made'))
        else:
            self.stdout.write(self.style.SUCCESS('Running in IMPORT mode - database changes will be saved'))
        
        try:
            if not self.dry_run:
                # Real import: wrap everything in a transaction
                with transaction.atomic():
                    self._run_import_steps()
            else:
                # Dry-run: simulate without transaction
                self._run_import_steps()
        
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during import: {str(e)}'))
            if not self.dry_run:
                raise
        
        # Print statistics
        self.print_statistics()

    def _run_import_steps(self):
        """Run all import steps"""
        # Step 1: Import employees (EmployeeProfile + CollaborationRate)
        self.import_employees()
        
        # Step 2: Import time entries
        self.import_time_entries()
        
        # Step 3: Build job map for payouts
        self.build_job_map()
        
        # Step 4: Import payouts
        self.import_payouts()
        
        # Step 5: Create missing hourly payouts for time entries
        self.create_missing_hourly_payouts()
        
        # Step 6: Import payroll settings
        self.import_payroll_settings()

    def import_employees(self):
        """Import employees from employees_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'employees_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Employees CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[1/5] Importing employees...')
        
        # Get existing users by email
        existing_users = {u.email.lower(): u for u in User.objects.filter(email__isnull=False)}
        existing_profiles = {str(ep.user.id): ep for ep in EmployeeProfile.objects.select_related('user').all()}
        
        profiles_to_create = []
        profiles_to_update = []
        collaboration_rates_to_create = []
        profile_created_at_map = {}  # Map employee_id -> created_at from CSV
        
        # Get existing collaboration rates to prevent duplicates
        existing_collab_rates = set(
            CollaborationRate.objects.values_list('employee_id', 'member_count', flat=False)
        )
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    employee_id = row.get('id', '').strip()
                    email = row.get('email', '').strip()
                    name = row.get('name', '').strip()
                    phone = row.get('phone', '').strip()
                    department = row.get('department', '').strip()
                    position = row.get('position', '').strip()
                    status = row.get('status', 'active').strip()
                    pay_scale_type = row.get('pay_scale_type', 'hourly').strip()
                    hourly_rate = row.get('hourly_rate', '').strip()
                    timezone_str = row.get('timezone', 'America/Chicago').strip()
                    is_admin = row.get('is_admin', 'false').lower() == 'true'
                    created_at_str = row.get('created_at', '').strip()
                    
                    if not employee_id or not email:
                        continue
                    
                    # Find or create user
                    user = existing_users.get(email.lower())
                    if not user:
                        # User doesn't exist, skip (users should be imported first)
                        continue
                    
                    self.user_map[employee_id] = user
                    
                    # Check if profile exists
                    existing_profile = existing_profiles.get(str(user.id))
                    
                    # Parse hourly_rate
                    hourly_rate_decimal = None
                    if hourly_rate:
                        try:
                            hourly_rate_decimal = Decimal(hourly_rate)
                        except (InvalidOperation, ValueError):
                            pass
                    
                    # Parse created_at from CSV
                    if created_at_str:
                        try:
                            created_at_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            if timezone.is_naive(created_at_dt):
                                created_at_dt = timezone.make_aware(created_at_dt)
                            profile_created_at_map[employee_id] = created_at_dt
                        except:
                            pass
                    
                    if existing_profile:
                        # Update existing profile
                        existing_profile.phone = phone or existing_profile.phone
                        existing_profile.department = department or existing_profile.department
                        existing_profile.position = position or existing_profile.position
                        existing_profile.timezone = timezone_str
                        existing_profile.pay_scale_type = pay_scale_type
                        existing_profile.hourly_rate = hourly_rate_decimal
                        existing_profile.is_administrator = is_admin
                        existing_profile.status = status
                        if not self.dry_run:
                            profiles_to_update.append(existing_profile)
                        self.stats['employee_profiles_updated'] += 1
                    else:
                        # Create new profile
                        if not self.dry_run:
                            profile = EmployeeProfile(
                                id=uuid.UUID(employee_id),
                                user=user,
                                phone=phone,
                                department=department or 'General',
                                position=position or 'Employee',
                                timezone=timezone_str,
                                pay_scale_type=pay_scale_type,
                                hourly_rate=hourly_rate_decimal,
                                is_administrator=is_admin,
                                status=status,
                            )
                            profiles_to_create.append(profile)
                        self.stats['employee_profiles_created'] += 1
                    
                    # Import collaboration rates (project_rate_1_member, project_rate_2_members, etc.)
                    for member_count in range(1, 6):  # 1 to 5 members
                        rate_key = f'project_rate_{member_count}_member' if member_count == 1 else f'project_rate_{member_count}_members'
                        rate_value = row.get(rate_key, '').strip()
                        
                        if rate_value:
                            try:
                                rate_decimal = Decimal(rate_value)
                                collab_key = (user.id, member_count)
                                
                                if collab_key not in existing_collab_rates:
                                    if not self.dry_run:
                                        collaboration_rates_to_create.append(CollaborationRate(
                                            employee=user,
                                            member_count=member_count,
                                            percentage=rate_decimal,
                                        ))
                                    self.stats['collaboration_rates_created'] += 1
                                else:
                                    self.stats['collaboration_rates_skipped'] += 1
                            except (InvalidOperation, ValueError):
                                pass
                
                except Exception as e:
                    error_msg = f"Error importing employee {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create profiles
        if profiles_to_create and not self.dry_run:
            EmployeeProfile.objects.bulk_create(profiles_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            
            # Update created_at dates from CSV (bulk_create ignores auto_now_add values)
            if profile_created_at_map:
                profiles_to_update_dates = []
                employee_ids = list(profile_created_at_map.keys())
                fetched_profiles = {str(ep.id): ep for ep in EmployeeProfile.objects.filter(id__in=employee_ids)}
                
                for employee_id, created_at_dt in profile_created_at_map.items():
                    profile = fetched_profiles.get(employee_id)
                    if profile:
                        profile.created_at = created_at_dt
                        profiles_to_update_dates.append(profile)
                
                if profiles_to_update_dates:
                    EmployeeProfile.objects.bulk_update(profiles_to_update_dates, ['created_at'], batch_size=self.batch_size)
        
        # Bulk update profiles
        if profiles_to_update and not self.dry_run:
            EmployeeProfile.objects.bulk_update(
                profiles_to_update,
                ['phone', 'department', 'position', 'timezone', 'pay_scale_type', 
                 'hourly_rate', 'is_administrator', 'status'],
                batch_size=self.batch_size
            )
        
        # Bulk create collaboration rates
        if collaboration_rates_to_create and not self.dry_run:
            CollaborationRate.objects.bulk_create(
                collaboration_rates_to_create, 
                batch_size=self.batch_size, 
                ignore_conflicts=True
            )
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Employee profiles: {self.stats["employee_profiles_created"]} created, '
            f'{self.stats["employee_profiles_updated"]} updated'
        ))
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Collaboration rates: {self.stats["collaboration_rates_created"]} created, '
            f'{self.stats["collaboration_rates_skipped"]} skipped'
        ))

    def import_time_entries(self):
        """Import time entries from time_entries_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'time_entries_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Time entries CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[2/5] Importing time entries...')
        
        # Get existing time entry IDs to prevent duplicates
        existing_entry_ids = set(TimeEntry.objects.values_list('id', flat=True))
        
        entries_to_create = []
        entry_created_at_map = {}  # Map entry_id -> created_at from CSV
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry_id = row.get('id', '').strip()
                    employee_id = row.get('employee_id', '').strip()
                    check_in_time = row.get('check_in_time', '').strip()
                    check_out_time = row.get('check_out_time', '').strip()
                    total_hours = row.get('total_hours', '').strip()
                    status = row.get('status', 'checked_in').strip()
                    notes = row.get('notes', '').strip()
                    created_at_str = row.get('created_at', '').strip()
                    
                    if not entry_id or not employee_id or not check_in_time:
                        continue
                    
                    # Check for duplicates
                    if entry_id in existing_entry_ids:
                        self.stats['time_entries_skipped'] += 1
                        continue
                    
                    try:
                        entry_uuid = uuid.UUID(entry_id)
                    except ValueError:
                        continue
                    
                    # Get user
                    user = self.user_map.get(employee_id)
                    if not user:
                        continue
                    
                    # Parse dates
                    try:
                        check_in = datetime.fromisoformat(check_in_time.replace('Z', '+00:00'))
                        if timezone.is_naive(check_in):
                            check_in = timezone.make_aware(check_in)
                    except:
                        continue
                    
                    check_out = None
                    if check_out_time:
                        try:
                            check_out = datetime.fromisoformat(check_out_time.replace('Z', '+00:00'))
                            if timezone.is_naive(check_out):
                                check_out = timezone.make_aware(check_out)
                        except:
                            pass
                    
                    # Parse total_hours
                    total_hours_decimal = None
                    if total_hours:
                        try:
                            total_hours_decimal = Decimal(total_hours)
                        except (InvalidOperation, ValueError):
                            pass
                    
                    # Parse created_at from CSV
                    created_at_dt = None
                    if created_at_str:
                        try:
                            created_at_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                            if timezone.is_naive(created_at_dt):
                                created_at_dt = timezone.make_aware(created_at_dt)
                            entry_created_at_map[entry_id] = created_at_dt
                        except:
                            pass
                    
                    if not self.dry_run:
                        entries_to_create.append(TimeEntry(
                            id=entry_uuid,
                            employee=user,
                            check_in_time=check_in,
                            check_out_time=check_out,
                            total_hours=total_hours_decimal,
                            status=status if status in ['checked_in', 'checked_out'] else 'checked_in',
                            notes=notes or None,
                        ))
                    
                    self.stats['time_entries_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing time entry {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create time entries
        if entries_to_create and not self.dry_run:
            TimeEntry.objects.bulk_create(entries_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            
            # Update created_at dates from CSV (bulk_create ignores auto_now_add values)
            if entry_created_at_map:
                entries_to_update = []
                entry_ids = list(entry_created_at_map.keys())
                fetched_entries = {str(e.id): e for e in TimeEntry.objects.filter(id__in=entry_ids)}
                
                for entry_id, created_at_dt in entry_created_at_map.items():
                    entry = fetched_entries.get(entry_id)
                    if entry:
                        entry.created_at = created_at_dt
                        entries_to_update.append(entry)
                
                if entries_to_update:
                    TimeEntry.objects.bulk_update(entries_to_update, ['created_at'], batch_size=self.batch_size)
            
            # Build time_entry_map for linking hourly payouts
            # Map by (employee_id, check_in_time, check_out_time)
            all_time_entries = TimeEntry.objects.select_related('employee').all()
            for entry in all_time_entries:
                # Create a key from employee_id and times (normalize times for matching)
                check_in_key = entry.check_in_time.isoformat() if entry.check_in_time else None
                check_out_key = entry.check_out_time.isoformat() if entry.check_out_time else None
                map_key = (str(entry.employee.id), check_in_key, check_out_key)
                self.time_entry_map[map_key] = entry
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Time entries: {self.stats["time_entries_created"]} created, '
            f'{self.stats["time_entries_skipped"]} skipped'
        ))

    def build_job_map(self):
        """Build job map from existing jobs for linking payouts"""
        self.stdout.write('\n[3/5] Building job map...')
        
        # Get all jobs by ID
        jobs = Job.objects.all()
        for job in jobs:
            self.job_map[str(job.id)] = job
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Job map: {len(self.job_map)} jobs loaded'
        ))

    def import_payouts(self):
        """Import payouts from payouts_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'payouts_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'Payouts CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[4/5] Importing payouts...')
        
        # Get existing payout IDs to prevent duplicates
        existing_payout_ids = set(Payout.objects.values_list('id', flat=True))
        
        payouts_to_create = []
        payout_created_at_map = {}  # Map payout_id -> created_at from CSV
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    payout_id = row.get('id', '').strip()
                    employee_id = row.get('employee_id', '').strip()
                    employee_name = row.get('employee_name', '').strip()
                    calculation_type = row.get('calculation_type', 'project').strip()
                    amount = row.get('amount', '').strip()
                    rate = row.get('rate', '').strip()
                    project_value = row.get('project_value', '').strip()
                    project_title = row.get('project_title', '').strip()
                    is_first_time = row.get('is_first_time', 'false').lower() == 'true'
                    job_id = row.get('job_id', '').strip()
                    created_at = row.get('created_at', '').strip()
                    source = row.get('source', 'auto').strip().lower()
                    clock_in_time = row.get('clock_in_time', '').strip()  # For hourly payouts
                    clock_out_time = row.get('clock_out_time', '').strip()  # For hourly payouts
                    
                    if not payout_id or not employee_id or not amount:
                        continue
                    
                    # Check for duplicates
                    if payout_id in existing_payout_ids:
                        self.stats['payouts_skipped'] += 1
                        continue
                    
                    try:
                        payout_uuid = uuid.UUID(payout_id)
                    except ValueError:
                        continue
                    
                    # Get user
                    user = self.user_map.get(employee_id)
                    if not user:
                        continue
                    
                    # Parse amounts
                    try:
                        amount_decimal = Decimal(amount)
                    except (InvalidOperation, ValueError):
                        continue
                    
                    rate_decimal = None
                    if rate:
                        try:
                            rate_decimal = Decimal(rate)
                        except (InvalidOperation, ValueError):
                            pass
                    
                    project_value_decimal = None
                    if project_value:
                        try:
                            project_value_decimal = Decimal(project_value)
                        except (InvalidOperation, ValueError):
                            pass
                    
                    # Determine payout type - check employee_name first (contains bonus info)
                    payout_type = 'project'
                    if 'First Time Bonus' in employee_name or 'First Time Bonus' in project_title or is_first_time:
                        payout_type = 'bonus_first_time'
                    elif 'Quoted By Bonus' in employee_name or 'Quoted By Bonus' in project_title:
                        payout_type = 'bonus_quoted_by'
                    elif calculation_type == 'hourly':
                        payout_type = 'hourly'
                    
                    # Validate source
                    if source not in ['auto', 'manual']:
                        source = 'auto'
                    
                    # Get job if available
                    job = None
                    if job_id:
                        job = self.job_map.get(job_id)
                    
                    # Get time_entry if available (for hourly payouts)
                    time_entry = None
                    if payout_type == 'hourly' and clock_in_time:
                        try:
                            # Parse clock_in_time and clock_out_time from payout CSV
                            clock_in_dt = datetime.fromisoformat(clock_in_time.replace('Z', '+00:00'))
                            if timezone.is_naive(clock_in_dt):
                                clock_in_dt = timezone.make_aware(clock_in_dt)
                            
                            clock_out_dt = None
                            if clock_out_time:
                                try:
                                    clock_out_dt = datetime.fromisoformat(clock_out_time.replace('Z', '+00:00'))
                                    if timezone.is_naive(clock_out_dt):
                                        clock_out_dt = timezone.make_aware(clock_out_dt)
                                except:
                                    pass
                            
                            # Create map key to find matching time entry
                            clock_in_key = clock_in_dt.isoformat() if clock_in_dt else None
                            clock_out_key = clock_out_dt.isoformat() if clock_out_dt else None
                            map_key = (employee_id, clock_in_key, clock_out_key)
                            
                            # Try to find matching time entry
                            time_entry = self.time_entry_map.get(map_key)
                            
                            if not time_entry:
                                # Try fuzzy matching - find time entry by employee and approximate times
                                # (within 1 minute tolerance for clock_in_time)
                                for (emp_id, check_in_key, check_out_key), entry in self.time_entry_map.items():
                                    if emp_id == employee_id:
                                        try:
                                            if check_in_key:
                                                check_in_dt = datetime.fromisoformat(check_in_key)
                                                # Check if times are within 1 minute of each other
                                                if abs((clock_in_dt - check_in_dt).total_seconds()) <= 60:
                                                    # Also check check_out_time if both exist
                                                    if clock_out_dt and check_out_key:
                                                        check_out_dt_from_entry = datetime.fromisoformat(check_out_key)
                                                        if abs((clock_out_dt - check_out_dt_from_entry).total_seconds()) <= 60:
                                                            time_entry = entry
                                                            break
                                                    elif not clock_out_dt and not check_out_key:
                                                        # Both are None, match
                                                        time_entry = entry
                                                        break
                                                    elif not clock_out_dt or not check_out_key:
                                                        # One is None, still match if check_in is close
                                                        time_entry = entry
                                                        break
                                        except:
                                            continue
                            
                        except Exception as e:
                            # If parsing fails, continue without time_entry
                            pass
                    
                    # Parse created_at from CSV
                    created_at_dt = None
                    if created_at:
                        try:
                            created_at_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            if timezone.is_naive(created_at_dt):
                                created_at_dt = timezone.make_aware(created_at_dt)
                            payout_created_at_map[payout_id] = created_at_dt
                        except:
                            pass
                    
                    if not self.dry_run:
                        # Create payout - store project_title for service names when job is not linked
                        payout = Payout(
                            id=payout_uuid,
                            employee=user,
                            payout_type=payout_type,
                            amount=amount_decimal,
                            job=job,
                            time_entry=time_entry,  # Link to time entry for hourly payouts
                            project_value=project_value_decimal,
                            rate_percentage=rate_decimal,
                            project_title=project_title or None,  # Store service names here
                            source=source,
                        )
                        payouts_to_create.append(payout)
                    
                    self.stats['payouts_created'] += 1
                
                except Exception as e:
                    error_msg = f"Error importing payout {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create payouts
        if payouts_to_create and not self.dry_run:
            Payout.objects.bulk_create(payouts_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            
            # Update created_at dates from CSV (bulk_create ignores auto_now_add values)
            if payout_created_at_map:
                payouts_to_update = []
                payout_ids = list(payout_created_at_map.keys())
                fetched_payouts = {str(p.id): p for p in Payout.objects.filter(id__in=payout_ids)}
                
                for payout_id, created_at_dt in payout_created_at_map.items():
                    payout = fetched_payouts.get(payout_id)
                    if payout:
                        payout.created_at = created_at_dt
                        payouts_to_update.append(payout)
                
                if payouts_to_update:
                    Payout.objects.bulk_update(payouts_to_update, ['created_at'], batch_size=self.batch_size)
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Payouts: {self.stats["payouts_created"]} created, '
            f'{self.stats["payouts_skipped"]} skipped'
        ))

    def create_missing_hourly_payouts(self):
        """Create hourly payouts for time entries that don't have a payout yet"""
        self.stdout.write('\n[5/6] Creating missing hourly payouts for time entries...')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('  (Skipped in dry-run mode)'))
            return
        
        # Get all time entries that are checked out and have total_hours
        time_entries_without_payouts = TimeEntry.objects.filter(
            status='checked_out',
            total_hours__isnull=False,
            total_hours__gt=0
        ).select_related('employee__employee_profile').prefetch_related('payouts')
        
        payouts_to_create = []
        time_entry_created_at_map = {}  # Map time_entry_id -> created_at for updating payouts
        
        for time_entry in time_entries_without_payouts:
            # Check if this time entry already has an hourly payout
            existing_payout = time_entry.payouts.filter(payout_type='hourly').first()
            if existing_payout:
                continue  # Already has a payout, skip
            
            # Check if employee has an hourly rate
            try:
                profile = time_entry.employee.employee_profile
                if profile.pay_scale_type != 'hourly' or not profile.hourly_rate:
                    continue  # Not an hourly employee or no hourly rate set
            except EmployeeProfile.DoesNotExist:
                continue  # No employee profile, skip
            
            # Calculate payout amount
            try:
                amount = (time_entry.total_hours * profile.hourly_rate).quantize(Decimal('0.01'))
                
                # Store time_entry created_at for later update
                if time_entry.created_at:
                    time_entry_created_at_map[str(time_entry.id)] = time_entry.created_at
                
                # Create payout
                payout = Payout(
                    employee=time_entry.employee,
                    payout_type='hourly',
                    amount=amount,
                    time_entry=time_entry,
                    rate_percentage=profile.hourly_rate,
                    source='auto',
                    notes=f"Auto-created hourly payout for {time_entry.total_hours} hours @ ${profile.hourly_rate}/hr"
                )
                payouts_to_create.append(payout)
                self.stats['payouts_auto_created'] += 1
                
            except Exception as e:
                error_msg = f"Error creating payout for time entry {time_entry.id}: {str(e)}"
                self.stats['errors'].append(error_msg)
                if len(self.stats['errors']) <= 10:
                    self.stdout.write(self.style.ERROR(error_msg))
        
        # Bulk create payouts
        if payouts_to_create:
            Payout.objects.bulk_create(payouts_to_create, batch_size=self.batch_size, ignore_conflicts=True)
            
            # Update created_at dates from time_entry (bulk_create ignores auto_now_add values)
            if time_entry_created_at_map:
                payouts_to_update = []
                # Convert string IDs to UUIDs for querying
                time_entry_uuids = [uuid.UUID(tid) for tid in time_entry_created_at_map.keys()]
                
                # Fetch all payouts we just created in one query
                fetched_payouts = Payout.objects.filter(
                    time_entry_id__in=time_entry_uuids,
                    payout_type='hourly',
                    source='auto'
                )
                
                # Match payouts to their time_entry created_at dates
                for payout in fetched_payouts:
                    time_entry_id_str = str(payout.time_entry_id)
                    if time_entry_id_str in time_entry_created_at_map:
                        payout.created_at = time_entry_created_at_map[time_entry_id_str]
                        payouts_to_update.append(payout)
                
                if payouts_to_update:
                    Payout.objects.bulk_update(payouts_to_update, ['created_at'], batch_size=self.batch_size)
            
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ Auto-created {len(payouts_to_create)} hourly payouts for time entries'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '  ✓ No missing hourly payouts to create'
            ))

    def import_payroll_settings(self):
        """Import payroll settings from app_settings_rows.csv"""
        csv_path = os.path.join(self.csv_dir, 'app_settings_rows.csv')
        if not os.path.exists(csv_path):
            self.stdout.write(self.style.WARNING(f'App settings CSV not found: {csv_path}'))
            return
        
        self.stdout.write('\n[6/6] Importing payroll settings...')
        
        settings = PayrollSettings.get_settings()
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    setting_key = row.get('setting_key', '').strip()
                    setting_value = row.get('setting_value', '').strip()
                    setting_type = row.get('setting_type', '').strip()
                    
                    if not setting_key or not setting_value:
                        continue
                    
                    if setting_key == 'first_time_bonus_percentage':
                        try:
                            settings.first_time_bonus_percentage = Decimal(setting_value)
                        except (InvalidOperation, ValueError):
                            pass
                    elif setting_key == 'quoted_by_bonus_percentage':
                        try:
                            settings.quoted_by_bonus_percentage = Decimal(setting_value)
                        except (InvalidOperation, ValueError):
                            pass
                
                except Exception as e:
                    error_msg = f"Error importing setting {row.get('id', 'unknown')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    if len(self.stats['errors']) <= 10:
                        self.stdout.write(self.style.ERROR(error_msg))
        
        if not self.dry_run:
            settings.save()
            self.stats['settings_updated'] = 1
        
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ Payroll settings: Updated'
        ))

    def print_statistics(self):
        """Print import statistics"""
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('Import Statistics'))
        self.stdout.write('=' * 80)
        
        self.stdout.write(f'\nEmployee Profiles:')
        self.stdout.write(f'  Created: {self.stats["employee_profiles_created"]}')
        self.stdout.write(f'  Updated: {self.stats["employee_profiles_updated"]}')
        
        self.stdout.write(f'\nCollaboration Rates:')
        self.stdout.write(f'  Created: {self.stats["collaboration_rates_created"]}')
        self.stdout.write(f'  Skipped: {self.stats["collaboration_rates_skipped"]}')
        
        self.stdout.write(f'\nTime Entries:')
        self.stdout.write(f'  Created: {self.stats["time_entries_created"]}')
        self.stdout.write(f'  Skipped: {self.stats["time_entries_skipped"]}')
        
        self.stdout.write(f'\nPayouts:')
        self.stdout.write(f'  Created: {self.stats["payouts_created"]}')
        self.stdout.write(f'  Auto-created: {self.stats["payouts_auto_created"]}')
        self.stdout.write(f'  Skipped: {self.stats["payouts_skipped"]}')
        
        self.stdout.write(f'\nSettings:')
        self.stdout.write(f'  Updated: {self.stats["settings_updated"]}')
        
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

