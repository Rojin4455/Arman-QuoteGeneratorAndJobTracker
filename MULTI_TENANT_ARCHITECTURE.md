# Multi-Tenant Architecture Proposal

## Executive Summary

This document outlines the architecture for converting your single-company service management system into a multi-tenant SaaS platform. The recommended approach is **Shared Database with Tenant Isolation** using a `Company` model and automatic query filtering.

---

## 1. Architecture Approach

### Recommended: Shared Database with Tenant ID (Row-Level Security)

**Why this approach?**
- ✅ Cost-effective: Single database, easier maintenance
- ✅ Scalable: Can handle hundreds of companies efficiently
- ✅ Easier migrations: No need to manage multiple databases
- ✅ Simpler deployment: One codebase, one database
- ✅ Good performance: Proper indexing makes filtering fast

**Alternative approaches considered:**
- ❌ Separate databases per tenant: Too complex, harder to manage
- ❌ Schema-based isolation: PostgreSQL-specific, migration complexity

---

## 2. Core Components

### 2.1 Company/Tenant Model

Create a new `Company` model that will be the root of all tenant isolation:

```python
# service_app/models.py (add to existing)

class Company(models.Model):
    """Multi-tenant company model"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-friendly identifier")
    subdomain = models.CharField(max_length=100, unique=True, null=True, blank=True)
    
    # Company details
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    
    # Subscription/Billing
    subscription_status = models.CharField(
        max_length=20,
        choices=[
            ('trial', 'Trial'),
            ('active', 'Active'),
            ('suspended', 'Suspended'),
            ('cancelled', 'Cancelled'),
        ],
        default='trial'
    )
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    
    # Settings
    timezone = models.CharField(max_length=50, default='America/Chicago')
    currency = models.CharField(max_length=10, default='USD')
    currency_symbol = models.CharField(max_length=5, default='$')
    
    # GHL Integration (existing company_id from GHL)
    ghl_company_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    
    # Metadata
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'companies'
        verbose_name_plural = 'Companies'
        ordering = ['name']
    
    def __str__(self):
        return self.name
```

### 2.2 Update User Model

Link users to companies:

```python
# service_app/models.py - Update User model

class User(AbstractUser):
    # ... existing fields ...
    
    # Add company relationship
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='users',
        null=True,  # Allow null during migration
        blank=True
    )
    
    # Keep existing role field
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_WORKER)
    
    # ... rest of existing code ...
```

### 2.3 Tenant-Aware Base Model

Create a mixin for automatic tenant filtering:

```python
# service_app/models.py (or create a new file: service_app/mixins.py)

class TenantAwareMixin(models.Model):
    """Mixin to add company relationship to models"""
    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='%(class)s_set',  # Dynamic related name
        null=True,  # Allow null during migration
        blank=True
    )
    
    class Meta:
        abstract = True
```

### 2.4 Tenant Context Middleware

Create middleware to automatically set tenant context:

```python
# service_app/middleware.py (new file)

from django.utils.deprecation import MiddlewareMixin
from django.db import connection
from .models import Company

class TenantMiddleware(MiddlewareMixin):
    """
    Middleware to set tenant context from:
    1. Subdomain (e.g., company1.yourdomain.com)
    2. Header (X-Company-ID or X-Company-Slug)
    3. JWT token (company_id claim)
    4. User's company (fallback)
    """
    
    def process_request(self, request):
        company = None
        
        # Method 1: Check subdomain
        host = request.get_host().split(':')[0]
        subdomain = host.split('.')[0] if '.' in host else None
        if subdomain and subdomain not in ['www', 'api', 'admin']:
            try:
                company = Company.objects.get(subdomain=subdomain, is_active=True)
            except Company.DoesNotExist:
                pass
        
        # Method 2: Check header
        if not company:
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
        
        # Method 3: Check JWT token (if available)
        if not company and hasattr(request, 'user') and request.user.is_authenticated:
            # Extract from token if available
            # This would require custom JWT payload
            pass
        
        # Method 4: Fallback to user's company
        if not company and hasattr(request, 'user') and request.user.is_authenticated:
            if hasattr(request.user, 'company') and request.user.company:
                company = request.user.company
        
        # Set on request for use in views
        request.company = company
        
        return None
```

### 2.5 Tenant-Aware QuerySet Manager

Create a custom manager to automatically filter by tenant:

```python
# service_app/managers.py (new file)

from django.db import models
from django.db.models import Q

class TenantQuerySet(models.QuerySet):
    """QuerySet that automatically filters by tenant"""
    
    def for_company(self, company):
        """Filter queryset to a specific company"""
        if company is None:
            return self.none()
        return self.filter(company=company)
    
    def for_request(self, request):
        """Filter queryset using company from request"""
        company = getattr(request, 'company', None)
        return self.for_company(company)

class TenantManager(models.Manager):
    """Manager that uses TenantQuerySet"""
    
    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)
    
    def for_company(self, company):
        return self.get_queryset().for_company(company)
    
    def for_request(self, request):
        return self.get_queryset().for_request(request)
```

---

## 3. Model Updates Required

### 3.1 Models That Need Company Relationship

All these models should inherit from `TenantAwareMixin` or add `company` field:

**service_app:**
- ✅ Service
- ✅ Package
- ✅ Location
- ✅ Question
- ✅ QuestionOption
- ✅ SubQuestion
- ✅ Feature
- ✅ GlobalBasePrice (or make it company-specific)
- ✅ GlobalSizePackage (or make it company-specific)
- ✅ Order

**jobtracker_app:**
- ✅ Job
- ✅ JobServiceItem
- ✅ JobAssignment
- ✅ JobOccurrence

**payroll_app:**
- ✅ EmployeeProfile
- ✅ CollaborationRate
- ✅ TimeEntry
- ✅ Payout
- ✅ PayrollSettings (make it company-specific)

**quote_app:**
- ✅ CustomerSubmission
- ✅ QuoteSchedule
- ✅ CustomService
- ✅ CustomerServiceSelection
- ✅ CustomerQuestionResponse
- ✅ CustomerPackageQuote

**dashboard_app:**
- ✅ Invoice
- ✅ InvoiceItem

**accounts:**
- ✅ Contact (already has location_id, but needs company)
- ✅ Address
- ✅ GHLAuthCredentials (already has company_id, link it properly)
- ✅ Webhook

### 3.2 Example: Updated Service Model

```python
# service_app/models.py

class Service(TenantAwareMixin):  # Add mixin
    """Main service model"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Add custom manager
    objects = TenantManager()
    
    class Meta:
        db_table = 'services'
        ordering = ['order', 'name']
        # Add unique constraint per company
        unique_together = [['company', 'name']]  # Service names unique per company
    
    def __str__(self):
        return self.name
```

---

## 4. View Updates

### 4.1 Base ViewSet with Tenant Filtering

Create a base ViewSet that automatically filters by tenant:

```python
# service_app/views.py (add this)

class TenantAwareViewSet(viewsets.ModelViewSet):
    """Base ViewSet that automatically filters by tenant"""
    
    def get_queryset(self):
        queryset = super().get_queryset()
        company = getattr(self.request, 'company', None)
        
        if company is None:
            # If no company context, return empty queryset
            # Or raise an error depending on your requirements
            return queryset.none()
        
        # Filter by company
        if hasattr(queryset.model, 'company'):
            queryset = queryset.filter(company=company)
        
        return queryset
    
    def perform_create(self, serializer):
        """Automatically set company on create"""
        company = getattr(self.request, 'company', None)
        if company:
            serializer.save(company=company)
        else:
            serializer.save()
```

### 4.2 Update Existing Views

Example: Update Service views to use tenant filtering:

```python
# service_app/views.py

# Before:
class ServiceListCreateView(generics.ListCreateAPIView):
    queryset = Service.objects.all()
    # ...

# After:
class ServiceListCreateView(TenantAwareViewSet):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [IsAdminPermission]
    
    # get_queryset is inherited from TenantAwareViewSet
    # perform_create is inherited from TenantAwareViewSet
```

---

## 5. Authentication & Authorization Updates

### 5.1 Update JWT Token to Include Company

```python
# service_app/serializers.py or service_app/views.py

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        
        # Add company info to token
        if hasattr(user, 'company') and user.company:
            token['company_id'] = str(user.company.id)
            token['company_slug'] = user.company.slug
        
        return token
```

### 5.2 Update Permission Classes

```python
# service_app/views.py

class IsCompanyAdminPermission(permissions.BasePermission):
    """Permission to check if user is admin of the current company"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        company = getattr(request, 'company', None)
        if not company:
            return False
        
        # Check if user belongs to this company
        if not hasattr(request.user, 'company') or request.user.company != company:
            return False
        
        # Check if user is admin
        return request.user.is_admin
```

---

## 6. Migration Strategy

### Phase 1: Add Company Model (Non-Breaking)
1. Create `Company` model
2. Add `company` field to `User` (nullable)
3. Create migration
4. Create a default company for existing data
5. Assign all existing users to default company

### Phase 2: Add Company to All Models (Non-Breaking)
1. Add `TenantAwareMixin` to all models (company field nullable)
2. Create migrations
3. Backfill: Assign all existing records to default company

### Phase 3: Make Company Required (Breaking - Requires Downtime)
1. Make `company` field non-nullable on all models
2. Update all views to use tenant filtering
3. Test thoroughly
4. Deploy

### Phase 4: Add Tenant Middleware
1. Add `TenantMiddleware` to settings
2. Update all views to use tenant context
3. Test with multiple companies

---

## 7. Data Migration Script

```python
# service_app/management/commands/migrate_to_multi_tenant.py

from django.core.management.base import BaseCommand
from service_app.models import Company, User
from service_app.models import Service, Package, Location
from jobtracker_app.models import Job
# ... import all other models

class Command(BaseCommand):
    help = 'Migrate existing single-tenant data to multi-tenant structure'
    
    def handle(self, *args, **options):
        # Step 1: Create default company
        default_company, created = Company.objects.get_or_create(
            slug='default',
            defaults={
                'name': 'Default Company',
                'subdomain': 'default',
                'is_active': True
            }
        )
        
        self.stdout.write(f"Created/Found default company: {default_company.name}")
        
        # Step 2: Assign all users to default company
        users_updated = User.objects.filter(company__isnull=True).update(company=default_company)
        self.stdout.write(f"Assigned {users_updated} users to default company")
        
        # Step 3: Assign all tenant-aware models to default company
        models_to_migrate = [
            (Service, 'service_app'),
            (Package, 'service_app'),
            (Location, 'service_app'),
            (Job, 'jobtracker_app'),
            # ... add all other models
        ]
        
        for model, app_name in models_to_migrate:
            if hasattr(model, 'company'):
                updated = model.objects.filter(company__isnull=True).update(company=default_company)
                self.stdout.write(f"Assigned {updated} {model.__name__} records to default company")
        
        self.stdout.write(self.style.SUCCESS('Migration completed successfully!'))
```

---

## 8. API Changes

### 8.1 Company Selection

Clients need to specify company in one of these ways:

**Option A: Subdomain**
```
https://company1.yourdomain.com/api/service/services/
```

**Option B: Header**
```http
GET /api/service/services/
X-Company-ID: <uuid>
# OR
X-Company-Slug: company-slug
```

**Option C: JWT Token**
Include `company_id` in JWT token payload (automatic from user's company)

### 8.2 New Endpoints

```python
# service_app/urls.py

# Company management endpoints (super admin only)
path('companies/', CompanyViewSet.as_view({'get': 'list', 'post': 'create'})),
path('companies/<uuid:pk>/', CompanyViewSet.as_view({'get': 'retrieve', 'put': 'update'})),
path('companies/switch/', SwitchCompanyView.as_view()),  # For users with multiple companies
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```python
# service_app/tests.py

class MultiTenantTestCase(TestCase):
    def setUp(self):
        self.company1 = Company.objects.create(name="Company 1", slug="company1")
        self.company2 = Company.objects.create(name="Company 2", slug="company2")
        
        self.user1 = User.objects.create_user(
            username="user1",
            email="user1@company1.com",
            company=self.company1
        )
        
        self.service1 = Service.objects.create(
            name="Service 1",
            company=self.company1
        )
        
        self.service2 = Service.objects.create(
            name="Service 1",  # Same name, different company
            company=self.company2
        )
    
    def test_tenant_isolation(self):
        """Test that companies can't see each other's data"""
        services_company1 = Service.objects.for_company(self.company1)
        self.assertEqual(services_company1.count(), 1)
        self.assertEqual(services_company1.first(), self.service1)
        
        services_company2 = Service.objects.for_company(self.company2)
        self.assertEqual(services_company2.count(), 1)
        self.assertEqual(services_company2.first(), self.service2)
```

### 9.2 Integration Tests

Test API endpoints with different company contexts:

```python
def test_service_list_with_company_header(self):
    """Test that services are filtered by company header"""
    # Create services for different companies
    # Make API call with X-Company-ID header
    # Verify only correct company's services are returned
```

---

## 10. Security Considerations

### 10.1 Data Leakage Prevention

1. **Always filter by company** - Never return data without company filter
2. **Validate company in permissions** - Check user belongs to company
3. **Audit logs** - Track which company data was accessed
4. **Row-level security** - Use database constraints where possible

### 10.2 Cross-Tenant Access Prevention

```python
# service_app/permissions.py

class IsCompanyMember(permissions.BasePermission):
    """Ensure user belongs to the company in the request"""
    
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        company = getattr(request, 'company', None)
        if not company:
            return False
        
        # Super admin can access any company (optional)
        if hasattr(request.user, 'is_superuser') and request.user.is_superuser:
            return True
        
        # Regular users must belong to the company
        return (
            hasattr(request.user, 'company') and
            request.user.company == company
        )
```

---

## 11. Performance Optimization

### 11.1 Database Indexes

Add indexes on `company` field for all tenant-aware models:

```python
class Service(TenantAwareMixin):
    # ...
    class Meta:
        indexes = [
            models.Index(fields=['company', 'is_active']),
            models.Index(fields=['company', 'order']),
        ]
```

### 11.2 Query Optimization

Use `select_related` and `prefetch_related`:

```python
queryset = Service.objects.for_company(company).select_related('company')
```

---

## 12. Rollout Plan

### Week 1: Foundation
- [ ] Create Company model
- [ ] Add company field to User
- [ ] Create migration script
- [ ] Test with single company (backward compatible)

### Week 2: Model Updates
- [ ] Add TenantAwareMixin to all models
- [ ] Create migrations
- [ ] Backfill existing data
- [ ] Test data integrity

### Week 3: View Updates
- [ ] Create TenantAwareViewSet
- [ ] Update all viewsets
- [ ] Add tenant middleware
- [ ] Test API endpoints

### Week 4: Frontend Integration
- [ ] Update frontend to send company context
- [ ] Add company selection UI
- [ ] Test end-to-end flows
- [ ] Performance testing

### Week 5: Production Rollout
- [ ] Deploy to staging
- [ ] Create test companies
- [ ] Verify isolation
- [ ] Deploy to production
- [ ] Monitor for issues

---

## 13. Additional Considerations

### 13.1 Super Admin Access

Consider creating a super admin role that can access all companies:

```python
class User(AbstractUser):
    # ...
    is_superuser = models.BooleanField(default=False)  # Can access all companies
    company = models.ForeignKey(...)  # Default company
```

### 13.2 Company Settings

Create a CompanySettings model for company-specific configurations:

```python
class CompanySettings(models.Model):
    company = models.OneToOneField(Company, on_delete=models.CASCADE)
    # Company-specific settings
    invoice_prefix = models.CharField(max_length=10, default='INV')
    # ... other settings
```

### 13.3 Billing Integration

Plan for subscription management:

```python
class CompanySubscription(models.Model):
    company = models.OneToOneField(Company, on_delete=models.CASCADE)
    plan = models.CharField(max_length=50)  # 'basic', 'pro', 'enterprise'
    status = models.CharField(max_length=20)
    current_period_end = models.DateTimeField()
    # ... billing fields
```

---

## 14. Questions to Consider

1. **User Management**: Can users belong to multiple companies?
2. **Data Sharing**: Should any data be shared across companies?
3. **Billing**: How will you handle subscriptions per company?
4. **Onboarding**: How will new companies be created?
5. **Migration**: Do you need to support existing single-company deployments?
6. **Super Admin**: Do you need a super admin that can access all companies?

---

## 15. Next Steps

1. Review this architecture with your team
2. Decide on company identification method (subdomain vs header)
3. Create Company model and initial migration
4. Start with one app (e.g., service_app) as a pilot
5. Test thoroughly before rolling out to all apps
6. Plan for data migration of existing single-company data

---

## Conclusion

This architecture provides a solid foundation for multi-tenant support while maintaining backward compatibility during migration. The shared database approach is cost-effective and scalable for most SaaS applications.

**Estimated Implementation Time**: 4-6 weeks for full rollout
**Risk Level**: Medium (requires careful testing to prevent data leakage)
**Breaking Changes**: Minimal if migration is done in phases

