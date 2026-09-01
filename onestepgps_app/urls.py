from django.urls import path

from . import views

urlpatterns = [
    path('onestepgps/', views.OneStepGPSView.as_view(), name='onestepgps-webhook'),
    path('webhook/<str:location_id>/', views.OneStepGPSView.as_view(), name='onestepgps-webhook-location'),
    path('settings/', views.OneStepGPSIntegrationView.as_view(), name='onestepgps-settings'),
    path('settings/test/', views.OneStepGPSTestConnectionView.as_view(), name='onestepgps-test'),
    path('devices/', views.OneStepGPSDevicesView.as_view(), name='onestepgps-devices'),
    path('alerts/recent/', views.OneStepGPSAlertsRecentView.as_view(), name='onestepgps-alerts-recent'),
    path('alerts/counts/', views.OneStepGPSAlertsCountsView.as_view(), name='onestepgps-alerts-counts'),
]
