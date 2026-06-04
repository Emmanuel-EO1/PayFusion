from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    # Checkout — converts cart into orders
    path('checkout/', views.checkout, name='checkout'),

    # Payment flow
    path('summary/<int:transaction_id>/', views.payment_summary, name='payment_summary'),
    path('pay/<int:transaction_id>/', views.initialize_paystack_payment, name='initialize_paystack_payment'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),
    path('payment/success/<str:reference>/', views.payment_success, name='payment_success'),

    # Withdrawals
    path('withdraw/', views.withdrawal_page, name='withdrawal_page'),
    path('withdrawal/request/', views.request_withdrawal, name='request_withdrawal'),
]