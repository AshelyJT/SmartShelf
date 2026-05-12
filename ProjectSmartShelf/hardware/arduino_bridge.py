# arduino_bridge.py
import serial
import time
from django.conf import settings

_serial_connection = None

def get_serial_connection():
    global _serial_connection
    if _serial_connection is None or not _serial_connection.is_open:
        try:
            port = settings.ARDUINO_PORT
            baud = settings.ARDUINO_BAUD
            _serial_connection = serial.Serial(port, baud, timeout=1)
            time.sleep(2)  # Arduino reset delay
            print(f"Serial connection opened on {port}")

            # After connection, send current data for all mapped products
            send_initial_lcd_updates()

        except Exception as e:
            print(f"Failed to open serial port: {e}")
            return None
    return _serial_connection

def send_initial_lcd_updates():
    """Send current product info to all LCDs after a restart."""
    try:
        from checkout.models import Product
        from ProjectSmartShelf.models import ProductLCDMapping  # adjust import as needed
    except ImportError:
        # If the models aren't ready yet (e.g., during early startup), defer
        # This function is called after apps are loaded, so it should be safe.
        pass

    # Get all products that have an LCD mapping
    mappings = ProductLCDMapping.objects.select_related('product').all()
    for mapping in mappings:
        product = mapping.product
        # Use the existing function to send the data
        update_product_lcd(product)
        # Small delay between sends to avoid overwhelming the Arduino
        time.sleep(0.1)

def send_to_lcd(address, message):
    print(f"send_to_lcd called: address={address}, message={message}")   # <-- add
    if isinstance(address, int):
        address_hex = hex(address)
    else:
        address_hex = address
    full_message = f"{address_hex}:{message}\n"
    print(f"Full message: {full_message}")                               # <-- add
    conn = get_serial_connection()
    if conn:
        try:
            conn.write(full_message.encode('utf-8'))
            print(f"Written to serial: {full_message.strip()}")          # <-- add
        except Exception as e:
            print(f"Error writing: {e}")
    else:
        print("No serial connection")

def update_product_lcd(product):
    """Send product name and price to its assigned LCD."""
    # product is a Product instance, no need to import the model
    try:
        mapping = product.lcd_mapping
    except AttributeError:  # if no mapping exists
        return
    from ProjectSmartShelf.models import ScheduledPromotion
    from ProjectSmartShelf.utils import format_lcd_price

    address = mapping.i2c_address
    product_name = product.name
    if ScheduledPromotion.objects.filter(
        product=product,
        status=ScheduledPromotion.STATUS_ACTIVE,
    ).exists():
        product_name = f"{product.name} (PROMO)"
    message = f"{product_name}|{format_lcd_price(product.price)}"
    send_to_lcd(address, message)

def close_serial():
    """Close the serial connection (e.g., when Django stops)."""
    global _serial_connection
    if _serial_connection and _serial_connection.is_open:
        _serial_connection.close()
        print("Serial connection closed.")
    _serial_connection = None
