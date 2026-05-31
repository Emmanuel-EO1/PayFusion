from django.db import models
from PESOPay import settings
from tenants.models import Business
from django.utils.crypto import get_random_string 

def generate_transaction_reference():
    return 'PAYFUSION_' + get_random_string(12).upper() 

class Transaction(models.Model):

    TRANSACTION_TYPES = (
        ('credit', 'Credit'),
        ('debit', 'Debit'),
        ('payment', 'Payment'),      # Added to support master tracking state safely
        ('withdrawal', 'Withdrawal'), # Added to support withdrawal state safely
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='transactions')
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    reference = models.CharField(max_length=50, unique=True, default=generate_transaction_reference, editable=False)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.transaction_type.capitalize()} of {self.amount} with reference {self.reference} for {self.business.name}"
    
    def save(self, *args, **kwargs):
        # FIX: Removed the business balance calculations that were double-dipping!
        # The model now acts cleanly as an immutable historical record keeper.
        super().save(*args, **kwargs)