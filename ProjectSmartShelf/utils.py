# ProjectSmartShelf/utils.py
import paho.mqtt.publish as publish
from SmartShelf.settings import MQTT_BROKER_IP, MQTT_PORT
from checkout.models import Product


def send_price_update(lcd_id, product_name, new_price):
    topic = f"shelf/lcd{int(lcd_id)}/price"
    payload = f"{product_name}|{new_price}"
    try:
        publish.single(topic, payload, hostname=MQTT_BROKER_IP, port=MQTT_PORT)
        print(f"Published to {topic}: {payload}")
        return True
    except Exception as e:
        print(f"MQTT publish failed: {e}")
        return False

def send_all_prices_to_displays():
    """Send current price for every product to its assigned LCD."""
    products = Product.objects.all()
    for product in products:
        send_price_update(product.lcd_id, product.name, str(product.price))