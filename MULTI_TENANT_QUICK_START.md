# Multi-Tenant Quick Start Guide

## Overview

This is a quick reference guide for implementing multi-tenant support. For detailed architecture, see `MULTI_TENANT_ARCHITECTURE.md`.

---

## Step-by-Step Implementation

### Step 1: Create Company Model

```python
# service_app/models.py

import uuid
from django.db import models

class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    subdomain = models.CharField(max_length=100, unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'companies'
        verbose_name_plural = 'Companies'
    
    def __str__(self):
        return self.name
```

### Step 2: Create Tenant Mixin

```python
# service_app/mixins.py (new file)

from django.db import models

class TenantAwareMixin(models.Model):
    """Mixin to add company relationship to models"""
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='%(class)s_set',
        null=True,  # Allow null during migration
        blank=True
    )
    
    class Meta:
        abstract = True
```

### Step 3: Update User Model

```python
# service_app/models.py - Add to User model

class User(AbstractUser):
    # ... existing fields ...
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='users',
        null=True,  # Allow null during migration
        blank=True
    )
    # ... rest of existing code ...
```

### Step 4: Update One Model as Example

```python
# service_app/models.py - Update Service model

class Service(TenantAwareMixin):  # Add the mixin
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    # ... rest of existing fields ...
    
    class Meta:
        db_table = 'services'
        ordering = ['order', 'name']
        unique_together = [['company', 'name']]  # Names unique per company
```

### Step 5: Create Migrations

```bash
python manage.py makemigrations service_app
python manage.py migrate
```

### Step 6: Create Data Migration

```python
# service_app/management/commands/create_default_company.py

from django.core.management.base import BaseCommand
from service_app.models import Company, User

class Command(BaseCommand):
    help = 'Create default company and assign existing users'
    
    def handle(self, *args, **options):
        company, created = Company.objects.get_or_create(
            slug='default',
            defaults={
                'name': 'Default Company',
                'subdomain': 'default',
                'is_active': True
            }
        )
        
        User.objects.filter(company__isnull=True).update(company=company)
        self.stdout.write(f"Created default company and assigned users")
```

Run it:
```bash
python manage.py create_default_company
```

### Step 7: Create Tenant Middleware

```python
# service_app/middleware.py (new file)

from django.utils.deprecation import MiddlewareMixin
from .models import Company

class TenantMiddleware(MiddlewareMixin):
    def process_request(self, request):
        company = None
        
        # Method 1: Check header
        company_id = request.headers.get('X-Company-ID')
        company_slug = request.headers.get('X-Company-Slug')
        
        if company_id:
            try:
                company = Company.objects.get(id=company_id, is_active=True)
            except (Company.DoesNotExist, ValueError):
                pass
        elif company_slug:
            try:
                company = Company.objects.get(slug=company_slug, is_active=True)
            except Company.DoesNotExist:
                pass
        
        # Method 2: Fallback to user's company
        if not company and hasattr(request, 'user') and request.user.is_authenticated:
            if hasattr(request.user, 'company') and request.user.company:
                company = request.user.company
        
        request.company = company
        return None
```

### Step 8: Add Middleware to Settings

```python
# service_backend/settings.py

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'service_app.middleware.TenantMiddleware',  # Add this
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
```

### Step 9: Update Views to Filter by Company

```python
# service_app/views.py

class ServiceListCreateView(generics.ListCreateAPIView):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [IsAdminPermission]
    
    def get_queryset(self):
        queryset = super().get_queryset()
        company = getattr(self.request, 'company', None)
        
        if company:
            queryset = queryset.filter(company=company)
        else:
            queryset = queryset.none()  # Return empty if no company
        
        return queryset
    
    def perform_create(self, serializer):
        company = getattr(self.request, 'company', None)
        if company:
            serializer.save(company=company)
        else:
            raise ValidationError("Company context required")
```

### Step 10: Test It

```python
# Test in Django shell or API client

# Create two companies
company1 = Company.objects.create(name="Company 1", slug="company1")
company2 = Company.objects.create(name="Company 2", slug="company2")

# Create services for each
service1 = Service.objects.create(name="Service A", company=company1)
service2 = Service.objects.create(name="Service A", company=company2)  # Same name, different company

# Verify isolation
Service.objects.filter(company=company1).count()  # Should be 1
Service.objects.filter(company=company2).count()  # Should be 1
```

---

## API Usage Examples

### Using Header Method

```bash
# List services for a company
curl -H "X-Company-ID: <company-uuid>" \
     -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/service/services/

# Or using slug
curl -H "X-Company-Slug: company1" \
     -H "Authorization: Bearer <token>" \
     http://localhost:8000/api/service/services/
```

### Using Subdomain Method

```bash
# If using subdomain routing
curl -H "Authorization: Bearer <token>" \
     http://company1.yourdomain.com/api/service/services/
```

---

## Checklist for Each Model

When updating a model to be tenant-aware:

- [ ] Add `TenantAwareMixin` to model class
- [ ] Update `unique_together` to include `company` if needed
- [ ] Add database index on `company` field
- [ ] Update related views to filter by company
- [ ] Update serializers if needed
- [ ] Create migration
- [ ] Test isolation between companies

---

## Common Patterns

### Pattern 1: Base ViewSet for Tenant Filtering

```python
class TenantAwareViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        queryset = super().get_queryset()
        company = getattr(self.request, 'company', None)
        
        if company and hasattr(queryset.model, 'company'):
            queryset = queryset.filter(company=company)
        else:
            queryset = queryset.none()
        
        return queryset
    
    def perform_create(self, serializer):
        company = getattr(self.request, 'company', None)
        if company:
            serializer.save(company=company)
        else:
            raise ValidationError("Company context required")
```

### Pattern 2: Permission Check

```python
class IsCompanyMember(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        company = getattr(request, 'company', None)
        if not company:
            return False
        
        return (
            hasattr(request.user, 'company') and
            request.user.company == company
        )
```

### Pattern 3: Query Helper

```python
# service_app/utils.py

def get_company_queryset(model, request):
    """Helper to get company-filtered queryset"""
    company = getattr(request, 'company', None)
    if not company:
        return model.objects.none()
    
    if hasattr(model, 'company'):
        return model.objects.filter(company=company)
    
    return model.objects.all()
```

---

## Migration Order

1. **Phase 1** (Non-breaking):
   - Add Company model
   - Add company field to User (nullable)
   - Create default company
   - Assign users to default company

2. **Phase 2** (Non-breaking):
   - Add TenantAwareMixin to models (company nullable)
   - Backfill existing data to default company

3. **Phase 3** (Breaking - requires testing):
   - Make company field required (non-nullable)
   - Add tenant middleware
   - Update all views
   - Deploy

---

## Testing Checklist

- [ ] Create two companies
- [ ] Create data for each company
- [ ] Verify Company 1 can't see Company 2's data
- [ ] Verify Company 2 can't see Company 1's data
- [ ] Test API endpoints with company headers
- [ ] Test user authentication with company context
- [ ] Test data creation (auto-assigns company)
- [ ] Test data updates (can't change company)
- [ ] Test permissions (users can only access their company)

---

## Troubleshooting

### Issue: "Company context not found"
**Solution**: Ensure middleware is added and company header is sent

### Issue: "Users can see other companies' data"
**Solution**: Check that views are filtering by `request.company`

### Issue: "Migration fails"
**Solution**: Ensure company field is nullable initially, backfill data, then make required

### Issue: "Duplicate key errors"
**Solution**: Update `unique_together` to include `company` field

---

## Next Steps

1. Start with one model (Service) as a pilot
2. Test thoroughly
3. Gradually roll out to other models
4. Monitor for performance issues
5. Add indexes on company fields
6. Consider caching company context

