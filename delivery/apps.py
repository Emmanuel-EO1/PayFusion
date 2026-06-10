from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'delivery'

    def ready(self):
        # Import signals so they are registered when Django starts.
        # The signal auto-creates a Delivery record whenever
        # an Order is marked paid. Without this import,
        # the signal file exists but never gets connected.
        import delivery.signals