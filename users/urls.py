from django.urls import path
from django.contrib.auth import views as auth_views
from .forms import EmailAuthenticationForm
from . import views

app_name = 'users'

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='users/login.html',
        authentication_form=EmailAuthenticationForm
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/analytics/<int:business_id>/', views.business_analytics, name='business_analytics'),
    path('dashboard/earnings/<int:business_id>/', views.earnings_statement, name='earnings_statement'),

    path('history/', views.transaction_history, name='transaction_history'),
    path('sales/', views.sales_history, name='sales_history'),
    path('payouts/', views.payout_history, name='payout_history'),
]