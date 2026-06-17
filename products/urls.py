from django.urls import path
from . import views

app_name = 'products'

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('manage/', views.manage_products, name='manage_products'),
    path('manage/<int:product_id>/edit/', views.edit_product, name='edit_product'),
    path('<int:id>/', views.product_detail, name='product_detail'),
]