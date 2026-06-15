from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('admin/commission-analytics/', views.commission_analytics, name='commission_analytics'),
]