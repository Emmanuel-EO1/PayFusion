import logging

from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger('payfusion')


class User(AbstractUser):
    email = models.EmailField(unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email


# ============================================================
# USER PROFILE MODEL
#
# Extends the User model with customer-facing fields.
# OneToOne with User — auto-created via signal on user creation.
# Keeps the auth User model clean and minimal.
# ============================================================
class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile'
    )

    display_name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Name shown on reviews and public activity.'
    )

    phone = models.CharField(
        max_length=20,
        blank=True,
        default='',
    )

    # Email verification — set True when user clicks verification link.
    # Unverified users can browse but cannot checkout.
    is_email_verified = models.BooleanField(default=False)

    # Notification preferences
    notify_order_updates = models.BooleanField(
        default=True,
        help_text='Email me when my order status changes.'
    )
    notify_delivery_updates = models.BooleanField(
        default=True,
        help_text='Email me when my delivery status changes.'
    )
    notify_dispute_updates = models.BooleanField(
        default=True,
        help_text='Email me when my dispute status changes.'
    )
    notify_promotions = models.BooleanField(
        default=False,
        help_text='Email me about promotions and new products.'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profile — {self.user.email}'

    @property
    def name_display(self):
        """
        Returns display_name if set, otherwise falls back to
        the user's email prefix (before the @).
        Used in reviews, wishlists, and public-facing contexts.
        """
        if self.display_name:
            return self.display_name
        return self.user.email.split('@')[0]


# ============================================================
# DELIVERY ADDRESS MODEL
#
# A user can save multiple delivery addresses.
# One address is marked as default — pre-selected at checkout.
# Addresses are soft-referenced — never deleted even if user
# account changes, so order history remains accurate.
# ============================================================
class DeliveryAddress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='delivery_addresses'
    )

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    address_line_1 = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default='Nigeria')

    is_default = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', '-created_at']
        indexes = [
            models.Index(
                fields=['user', 'is_default'],
                name='address_user_default_idx'
            ),
        ]

    def __str__(self):
        return f'{self.full_name} — {self.city}, {self.state}'

    def save(self, *args, **kwargs):
        # When marking an address as default, unmark all others
        # for this user — only one default allowed at a time.
        if self.is_default:
            DeliveryAddress.objects.filter(
                user=self.user,
                is_default=True,
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


# ============================================================
# WISHLIST ITEM MODEL
#
# Tracks which products a user has saved for later.
# Using a through model (WishlistItem) rather than a plain
# ManyToManyField so we can store when the product was wishlisted.
# UniqueConstraint prevents the same product being added twice.
# ============================================================
class WishlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='wishlist'
    )

    product = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='wishlisted_by'
    )

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-added_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'product'],
                name='unique_wishlist_item'
            )
        ]
        indexes = [
            models.Index(
                fields=['user'],
                name='wishlist_user_idx'
            ),
        ]

    def __str__(self):
        return f'{self.user.email} — {self.product.name}'


# ============================================================
# SIGNAL — Auto-create UserProfile on user creation
# ============================================================
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
        logger.info(f'UserProfile created for {instance.email}')