from django.contrib import admin
from .models import Transaction

# Register your models here.

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):

    list_display = ('reference', 'business', 'amount', 'transaction_type',  'status', 'created_at')

    search_fields = ('reference', 'business__name')
