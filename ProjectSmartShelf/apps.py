import os
import joblib
from django.conf import settings
from django.apps import AppConfig

class ProjectsmartshelfConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ProjectSmartShelf'  # Note: matches the app folder name

    # Class-level attributes for ML artifacts (loaded once)
    model = None
    cat_encoder = None
    prod_encoder = None

    def ready(self):
        # Only run in the main server process, not the reloader
        if os.environ.get('RUN_MAIN'):
            # 1. Load ML models (only once)
            base = settings.BASE_DIR
            model_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'price_prediction_model.pkl')
            cat_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'category_encoder.pkl')
            prod_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'product_encoder.pkl')

            if os.path.exists(model_path):
                ProjectsmartshelfConfig.model = joblib.load(model_path)
                ProjectsmartshelfConfig.cat_encoder = joblib.load(cat_path)
                ProjectsmartshelfConfig.prod_encoder = joblib.load(prod_path)
                print("ML models loaded successfully.")
            else:
                print("ML files not found. Check paths.")

            # 2. Connect hardware signals and open serial port
            import ProjectSmartShelf.hardware.signals  # noqa
            from ProjectSmartShelf.hardware.arduino_bridge import get_serial_connection
            get_serial_connection()