from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import JobViewSet, OccurrenceListView, JobSeriesCreateView, JobBySeriesView,LocationJobListView,LocationJobDetailView,webhook_handler, AppointmentCalendarView, AppointmentViewSet, JobImageViewSet

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'appointments', AppointmentViewSet, basename='appointment')
router.register(r'job-images', JobImageViewSet, basename='job-image')

urlpatterns = [
    path('', include(router.urls)),
    path('occurrences/', OccurrenceListView.as_view(), name='occurrence-list'),
    path('appointments-calendar/', AppointmentCalendarView.as_view(), name='appointment-calendar'),
    path('jobs-series/', JobSeriesCreateView.as_view(), name='job-series-create'),
    path('jobs-series/<uuid:series_id>/', JobBySeriesView.as_view(), name='job-by-series'),
    path('locations/',LocationJobListView.as_view(), name='locations'),
    path('locations/jobs/',LocationJobDetailView.as_view(),name='locations-view'),
    path("webhook/", webhook_handler)
]