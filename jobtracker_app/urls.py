from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import JobViewSet, JTServiceViewSet, OccurrenceListView, JobSeriesCreateView, JobBySeriesView,LocationJobListView,LocationJobDetailView

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'services-templates', JTServiceViewSet, basename='jtservice')

urlpatterns = [
    path('', include(router.urls)),
    path('occurrences/', OccurrenceListView.as_view(), name='occurrence-list'),
    path('jobs-series/', JobSeriesCreateView.as_view(), name='job-series-create'),
    path('jobs-series/<uuid:series_id>/', JobBySeriesView.as_view(), name='job-by-series'),
    path('locations/',LocationJobListView.as_view(), name='locations'),
    path('locations/jobs/',LocationJobDetailView.as_view(),name='locations-view')
]