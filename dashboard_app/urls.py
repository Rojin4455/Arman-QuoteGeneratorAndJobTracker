from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import InvoiceViewSet, TechnicianWorkloadHeatmapView

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')

urlpatterns = [
    path('', include(router.urls)),
    path('technician-workload/', TechnicianWorkloadHeatmapView.as_view(), name='technician-workload'),
]