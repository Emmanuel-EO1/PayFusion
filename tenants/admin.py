from django.contrib import admin
from .models import Business, Wallet

# Register your models here.

@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'industry', 'state', 'country', 'created_at')
    search_fields = ('name', 'industry', 'state', 'country')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('business', 'balance', 'currency', 'created_at')
    search_fields = ('business__name',)