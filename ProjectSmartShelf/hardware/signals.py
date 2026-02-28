from django.db.models.signals import pre_save
from django.dispatch import receiver
from .arduino_bridge import update_product_lcd

@receiver(pre_save, sender='checkout.Product')
def product_price_update(sender, instance, **kwargs):
    # Only act on existing products (not new ones)
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            if old.price != instance.price:
                print(f"Price changed from {old.price} to {instance.price} for {instance.name}")
                update_product_lcd(instance)
        except sender.DoesNotExist:
            pass