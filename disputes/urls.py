from django.urls import path
from . import views

app_name = 'disputes'

urlpatterns = [
    path('raise/<str:order_reference>/', views.raise_dispute, name='raise_dispute'),
    path('<int:dispute_id>/', views.dispute_detail, name='dispute_detail'),
    path('my-disputes/', views.my_disputes, name='my_disputes'),
    path('vendor/<int:business_id>/', views.vendor_disputes, name='vendor_disputes'),
    path('<int:dispute_id>/vendor-note/', views.add_vendor_note, name='add_vendor_note'),
]