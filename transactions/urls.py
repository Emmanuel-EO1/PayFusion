from django.urls import path
from . import views
from transactions.views import paystack_webhook

app_name = 'transactions'

urlpatterns = [
    path('payment/webhook/', views.paystack_webhook, name='paystack_webhook'),
]