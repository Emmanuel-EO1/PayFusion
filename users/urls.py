from django.urls import path
from .views import RememberMeLoginView
from django.contrib.auth import views as auth_views
from .forms import EmailAuthenticationForm
from . import views

app_name = 'users'

urlpatterns = [
    # --- Authentication ---
    path('login/', RememberMeLoginView.as_view(
    template_name='users/login.html',
    authentication_form=EmailAuthenticationForm
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('verify-email/<str:token>/', views.verify_email, name='verify_email'),

    # --- Password Reset (Django built-ins) ---
    path('password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='users/password_reset.html',
            email_template_name='users/emails/password_reset_email.html',
            subject_template_name='users/emails/password_reset_subject.txt',
            success_url='/users/password-reset/done/',
        ),
        name='password_reset'
    ),
    path('password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='users/password_reset_done.html',
        ),
        name='password_reset_done'
    ),
    path('reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='users/password_reset_confirm.html',
            success_url='/users/reset/done/',
        ),
        name='password_reset_confirm'
    ),
    path('reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='users/password_reset_complete.html',
        ),
        name='password_reset_complete'
    ),

    # --- Vendor Dashboard ---
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/analytics/<int:business_id>/', views.business_analytics, name='business_analytics'),
    path('dashboard/earnings/<int:business_id>/', views.earnings_statement, name='earnings_statement'),

    # --- Customer Account ---
    path('profile/', views.customer_profile, name='customer_profile'),
    path('profile/addresses/', views.manage_addresses, name='manage_addresses'),
    path('profile/addresses/add/', views.add_address, name='add_address'),
    path('profile/addresses/<int:address_id>/edit/', views.edit_address, name='edit_address'),
    path('profile/addresses/<int:address_id>/delete/', views.delete_address, name='delete_address'),
    path('profile/addresses/<int:address_id>/set-default/', views.set_default_address, name='set_default_address'),
    path('wishlist/', views.wishlist, name='wishlist'),
    path('wishlist/add/<int:product_id>/', views.wishlist_add, name='wishlist_add'),
    path('wishlist/remove/<int:product_id>/', views.wishlist_remove, name='wishlist_remove'),

    # --- History ---
    path('history/', views.transaction_history, name='transaction_history'),
    path('sales/', views.sales_history, name='sales_history'),
    path('payouts/', views.payout_history, name='payout_history'),
]