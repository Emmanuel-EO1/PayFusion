from django.urls import path
from django.contrib.auth import views as auth_views
from .forms import EmailAuthenticationForm
from . import views

app_name = 'users'

urlpatterns = [
    # Auth
    path('login/', auth_views.LoginView.as_view(
        template_name='users/login.html',
        authentication_form=EmailAuthenticationForm
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # Dashboard
    path('dashboard/', views.dashboard, name='dashboard'),

    # Transaction history (customer)
    path('history/', views.transaction_history, name='transaction_history'),

    # Sales history (vendor)
    path('sales/', views.sales_history, name='sales_history'),

    # Payout history (vendor)
    path('payouts/', views.payout_history, name='payout_history'),
]