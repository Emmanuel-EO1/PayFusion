from django.urls import path
from . import views

app_name = 'cart'

urlpatterns = [
    path('', views.cart_detail, name='cart_detail'),
    path('add/<int:product_id>/', views.cart_add, name='cart_add'),

    # Simple products — no variant
    path('remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('update/<int:product_id>/', views.cart_update, name='cart_update'),

    # Varianted products — variant_id identifies the specific cart line
    path('remove/<int:product_id>/<int:variant_id>/', views.cart_remove, name='cart_remove_variant'),
    path('update/<int:product_id>/<int:variant_id>/', views.cart_update, name='cart_update_variant'),

    path('clear/', views.cart_clear, name='cart_clear'),
]