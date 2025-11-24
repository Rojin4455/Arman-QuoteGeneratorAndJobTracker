from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'employees', views.EmployeeProfileViewSet, basename='employee-profile')
router.register(r'time-entries', views.TimeEntryViewSet, basename='time-entry')
router.register(r'payouts', views.PayoutViewSet, basename='payout')
router.register(r'settings', views.PayrollSettingsViewSet, basename='payroll-settings')

urlpatterns = [
    path('', include(router.urls)),
    path('calculator/', views.CalculatorView.as_view(), name='calculator'),
    path('reports/', views.ReportsView.as_view(), name='reports'),
]
