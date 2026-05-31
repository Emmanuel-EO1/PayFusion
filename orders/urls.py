from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('checkout/', views.checkout, name='checkout'),
    path('summary/<int:transaction_id>/', views.payment_summary, name='payment_summary'),
    path('withdraw/', views.withdrawal_page, name='withdrawal_page'),
    path('withdrawal/request/', views.request_withdrawal, name='request_withdrawal'),
    path('pay/<int:transaction_id>/', views.initialize_paystack_payment, name='initialize_paystack_payment'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),
    path('payment/failed/<int:transaction_id>/', views.payment_failed, name='payment_failed'),
]