from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, UserProfile, DeliveryAddress, WishlistItem


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ('email', 'username', 'is_staff', 'is_active')
    ordering = ('email',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'display_name', 'phone', 'is_email_verified')
    list_filter = ('is_email_verified',)
    search_fields = ('user__email', 'display_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DeliveryAddress)
class DeliveryAddressAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'city', 'state', 'is_default')
    list_filter = ('state', 'is_default')
    search_fields = ('user__email', 'full_name', 'city')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'added_at')
    search_fields = ('user__email', 'product__name')
    readonly_fields = ('added_at',)