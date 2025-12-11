# Job Tracker Data Import Command

This management command imports job tracker data from CSV files into the database.

## CSV Files Required

Place the following CSV files in the specified directory (default: current directory):

1. `users_rows.csv` - User data
2. `services_rows.csv` - Service data (for mapping)
3. `jobs_rows.csv` - Job data
4. `job_services_rows.csv` - Job service items
5. `job_assignments_rows.csv` - Job assignments
6. `job_schedules_rows.csv` - Recurring job schedules
7. `accepted_quotes_rows.csv` - Accepted quotes (optional, for linking jobs to submissions)

## Usage

### Basic Usage (Dry-Run First!)

Always run in dry-run mode first to see what will be imported:

```bash
python manage.py import_jobtracker_data --dry-run
```

### Import with Custom CSV Directory

```bash
python manage.py import_jobtracker_data --csv-dir /path/to/csv/files
```

### Actual Import (No Dry-Run)

```bash
python manage.py import_jobtracker_data --csv-dir /path/to/csv/files
```

### Skip Certain Steps

```bash
# Skip user import (if users already exist)
python manage.py import_jobtracker_data --skip-users

# Skip service mapping
python manage.py import_jobtracker_data --skip-services
```

## What It Does

1. **Imports Users**: Creates or matches users from `users_rows.csv`
2. **Maps Services**: Matches CSV services to existing Service objects by name
   - Services that match → linked to existing Service
   - Services that don't match → treated as custom services (stored in `custom_name`)
3. **Imports Contacts**: Creates or matches contacts from job customer data
4. **Imports Addresses**: Creates addresses for contacts
5. **Links Submissions**: Attempts to link jobs to CustomerSubmission records
6. **Imports Jobs**: Creates Job records with all related data
7. **Imports Job Service Items**: Creates JobServiceItem records
8. **Imports Job Assignments**: Creates JobAssignment records
9. **Imports Job Schedules**: Updates recurring job information

## Important Notes

- **Always run with `--dry-run` first** to preview what will be imported
- The command uses transactions for safety - if any step fails, all changes are rolled back
- Existing jobs (by ID) are skipped to avoid duplicates
- Services are matched by name (case-insensitive)
- Contacts are matched by email, phone, or GHL contact ID
- Jobs are linked to submissions when customer info matches

## Field Mappings

### Status Mapping
- `pending` → `pending`
- `confirmed` → `confirmed`
- `in_progress` → `in_progress`
- `completed` → `completed`
- `cancelled` → `cancelled`

### Priority Mapping
- `1` → `low`
- `2` → `medium`
- `3` → `high`

### Frequency Mapping (for recurring jobs)
- `daily` → `day`
- `weekly` → `week`
- `monthly` → `month`
- `quarterly` → `quarter`
- `semi_annually` → `semi_annual`
- `yearly` → `year`

## Error Handling

- Errors are logged and displayed in the statistics
- The command continues processing even if individual records fail
- Up to 20 errors are displayed in detail
- All errors are counted in the final statistics

## Output

The command provides:
- Progress updates during import
- Statistics summary at the end
- Error list (if any)
- Success/failure status

## Example Output

```
================================================================================
Job Tracker Data Import
================================================================================
Running in DRY-RUN mode - no database changes

[1/8] Importing users...
  ✓ Users: 5 created, 3 matched

[2/8] Mapping services...
  ✓ Services: 45 matched, 12 marked as custom

...

================================================================================
Import Statistics
================================================================================
Users:
  Created: 5
  Matched: 3

Jobs:
  Created: 1234
  Skipped (already exist): 0
  Submissions linked: 456

...
```

