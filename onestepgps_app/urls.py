from django.urls import path

from . import fleet_views, views

urlpatterns = [
    path('onestepgps/', views.OneStepGPSView.as_view(), name='onestepgps-webhook'),
    path('webhook/<str:location_id>/', views.OneStepGPSView.as_view(), name='onestepgps-webhook-location'),
    path('dataqueue/<str:location_id>/', views.OneStepGPSDataQueueView.as_view(), name='onestepgps-dataqueue-location'),
    path('settings/', views.OneStepGPSIntegrationView.as_view(), name='onestepgps-settings'),
    path('settings/test/', views.OneStepGPSTestConnectionView.as_view(), name='onestepgps-test'),
    path('devices/', views.OneStepGPSDevicesView.as_view(), name='onestepgps-devices'),
    path('alerts/recent/', views.OneStepGPSAlertsRecentView.as_view(), name='onestepgps-alerts-recent'),
    path('alerts/counts/', views.OneStepGPSAlertsCountsView.as_view(), name='onestepgps-alerts-counts'),
    path('alerts/<uuid:pk>/acknowledge/', fleet_views.FleetAlertAcknowledgeView.as_view(), name='onestepgps-alert-ack'),
    path('fleet/summary/', fleet_views.FleetSummaryView.as_view(), name='onestepgps-fleet-summary'),
    path('fleet/trips/', fleet_views.FleetTripsView.as_view(), name='onestepgps-fleet-trips'),
    path('fleet/maintenance/', fleet_views.FleetMaintenanceView.as_view(), name='onestepgps-fleet-maintenance'),
    path('fleet/maintenance/<uuid:pk>/', fleet_views.FleetMaintenanceDetailView.as_view(), name='onestepgps-fleet-maintenance-detail'),
    path('fleet/maintenance/<uuid:pk>/complete/', fleet_views.FleetMaintenanceCompleteView.as_view(), name='onestepgps-fleet-maintenance-complete'),
    path('fleet/geofences/', fleet_views.FleetGeofenceListCreateView.as_view(), name='onestepgps-fleet-geofences'),
    path('fleet/geofences/<uuid:pk>/', fleet_views.FleetGeofenceDetailView.as_view(), name='onestepgps-fleet-geofence-detail'),
    path('fleet/assignments/', fleet_views.FleetAssignmentsView.as_view(), name='onestepgps-fleet-assignments'),
    path('fleet/reports/', fleet_views.FleetReportsView.as_view(), name='onestepgps-fleet-reports'),
]
