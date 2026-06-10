from django.urls import path
from . import views

app_name = 'delivery'

urlpatterns = [
    # Vendor
    path('orders/', views.vendor_orders, name='vendor_orders'),
    path('orders/<int:order_id>/accept/', views.accept_order, name='accept_order'),
    path('orders/<int:order_id>/ship/', views.ship_order, name='ship_order'),
    path('<int:delivery_id>/update/', views.update_delivery, name='update_delivery'),

    # Customer
    path('track/<str:order_reference>/', views.track_order, name='track_order'),
    path('confirm/<str:order_reference>/', views.confirm_delivery, name='confirm_delivery'),
]