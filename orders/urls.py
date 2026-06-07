from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    # Checkout
    path('checkout/', views.checkout, name='checkout'),

    # Payment flow
    path('summary/<int:transaction_id>/', views.payment_summary, name='payment_summary'),
    path('pay/<int:transaction_id>/', views.initialize_paystack_payment, name='initialize_paystack_payment'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),
    path('payment/success/<str:reference>/', views.payment_success, name='payment_success'),

    # Bank accounts
    path('bank-accounts/', views.bank_accounts, name='bank_accounts'),
    path('bank-accounts/add/', views.add_bank_account, name='add_bank_account'),
    path('bank-accounts/remove/<int:account_id>/', views.remove_bank_account, name='remove_bank_account'),

    # Withdrawals
    path('withdraw/', views.withdrawal_page, name='withdrawal_page'),
    path('withdrawal/request/', views.request_withdrawal, name='request_withdrawal'),
]