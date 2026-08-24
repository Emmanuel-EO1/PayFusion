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
    path('review/<int:product_id>/submit/', views.submit_review, name='submit_review'),
    path('review/<int:review_id>/edit/', views.edit_review, name='edit_review'),
    path('review/<int:review_id>/respond/', views.submit_response, name='submit_response'),
    path('review/<int:review_id>/hide/', views.hide_review, name='hide_review'),

    path('<int:id>/', views.product_detail, name='product_detail'),
]