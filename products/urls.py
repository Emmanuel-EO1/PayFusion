from django.urls import path
from . import views

app_name = 'products'

urlpatterns = [
    path('', views.product_list, name='product_list'),

    path('manage/', views.manage_products, name='manage_products'),
    path('manage/<int:product_id>/edit/', views.edit_product, name='edit_product'),

    path('manage/<int:product_id>/variants/', views.manage_variants, name='manage_variants'),
    path('manage/<int:product_id>/variants/add/', views.add_variant, name='add_variant'),
    path('manage/<int:product_id>/variants/<int:variant_id>/edit/', views.edit_variant, name='edit_variant'),
    path('manage/<int:product_id>/variants/bulk/', views.bulk_generate_variants, name='bulk_generate_variants'),
    path('manage/<int:product_id>/variants/bulk-edit/', views.bulk_edit_variants, name='bulk_edit_variants'),
    path('storefront/<int:business_id>/', views.storefront_settings, name='storefront_settings'),
    path('generate-description/<int:business_id>/', views.generate_description, name='generate_description'),
    path('create/<int:business_id>/', views.create_product, name='create_product'),

    path('<int:id>/', views.product_detail, name='product_detail'),
]