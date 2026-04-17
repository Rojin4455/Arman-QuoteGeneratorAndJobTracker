from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .admin_contact_views import AdminContactViewSet
from .views import InvoiceViewSet, TechnicianWorkloadHeatmapView

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'contacts', AdminContactViewSet, basename='dashboard-contact')

urlpatterns = [
    path('', include(router.urls)),
    path('technician-workload/', TechnicianWorkloadHeatmapView.as_view(), name='technician-workload'),
]