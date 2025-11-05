from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import JobViewSet, JTServiceViewSet, OccurrenceListView

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'services-templates', JTServiceViewSet, basename='jtservice')

urlpatterns = [
    path('', include(router.urls)),
    path('occurrences/', OccurrenceListView.as_view(), name='occurrence-list'),
]