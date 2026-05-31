from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.db.models.signals import post_save
from django.dispatch import receiver

# Create your models here.

class Business(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='businesses')
    
    name = models.CharField(max_length=250)
    slug = models.SlugField(unique=True)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    industry = models.CharField(max_length=150)
    address = models.CharField(max_length=250)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

class Wallet(models.Model):

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name='wallet')
    
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    currency = models.CharField(max_length=10, default='NGN')

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.business.name} Wallet'
    

@receiver(post_save, sender=Business)
def create_business_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(business=instance)
