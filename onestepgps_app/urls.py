from django.urls import path, include
from . import views


urlpatterns = [
    path('onestepgps/', views.OneStepGPSView.as_view(), name='onestepgps'),
]