# ProjectSmartShelf/utils.py
import paho.mqtt.publish as publish
from SmartShelf.settings import MQTT_BROKER_IP, MQTT_PORT
from checkout.models import Product


def format_lcd_price(price):
    return f"{float(price):.2f}"


def send_price_update(lcd_id, product_name, new_price, promotion_label=None):
    topic = f"shelf/lcd{int(lcd_id)}/price"
    display_name = product_name
    if promotion_label:
        display_name = f"{product_name} ({promotion_label})"
    payload = f"{display_name}|{format_lcd_price(new_price)}"
    try:
        publish.single(topic, payload, hostname=MQTT_BROKER_IP, port=MQTT_PORT)
        print(f"Published to {topic}: {payload}")
        return True
    except Exception as e:
        print(f"MQTT publish failed: {e}")
        return False

def send_all_prices_to_displays():
    """Send current price for every product to its assigned LCD."""
    from .models import ScheduledPromotion

    active_promotions = {
        promotion.product_id: promotion
        for promotion in ScheduledPromotion.objects.filter(
            status=ScheduledPromotion.STATUS_ACTIVE
        )
    }
    products = Product.objects.all()
    for product in products:
        if not product.lcd_id or product.lcd_id < 1:
            continue
        promotion_label = 'PROMO' if product.id in active_promotions else None
        if promotion_label:
            send_price_update(
                product.lcd_id,
                product.name,
                str(product.price),
                promotion_label=promotion_label,
            )
        else:
            send_price_update(product.lcd_id, product.name, str(product.price))
