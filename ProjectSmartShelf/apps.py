import os
import sys
import threading
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
    feature_columns = None
    fill_values = {}

    def ready(self):
        is_runserver = 'runserver' in sys.argv
        is_reloader_parent = (
            settings.DEBUG
            and is_runserver
            and '--noreload' not in sys.argv
            and os.environ.get('RUN_MAIN') != 'true'
        )
        if is_reloader_parent or 'test' in sys.argv:
            return

        # 1. Load ML models (only once)
        if ProjectsmartshelfConfig.model is None:
            base = settings.BASE_DIR
            model_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'price_recommendation_model.pkl')
            legacy_model_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'price_prediction_model.pkl')
            cat_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'category_encoder.pkl')
            prod_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'product_encoder.pkl')
            feature_columns_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'feature_columns.pkl')
            fill_values_path = os.path.join(base, 'ProjectSmartShelf', 'ml_models', 'fill_values.pkl')

            selected_model_path = model_path if os.path.exists(model_path) else legacy_model_path

            if os.path.exists(selected_model_path) and os.path.exists(cat_path) and os.path.exists(prod_path):
                ProjectsmartshelfConfig.model = joblib.load(selected_model_path)
                ProjectsmartshelfConfig.cat_encoder = joblib.load(cat_path)
                ProjectsmartshelfConfig.prod_encoder = joblib.load(prod_path)
                if os.path.exists(feature_columns_path):
                    ProjectsmartshelfConfig.feature_columns = joblib.load(feature_columns_path)
                elif hasattr(ProjectsmartshelfConfig.model, 'feature_names_in_'):
                    ProjectsmartshelfConfig.feature_columns = list(ProjectsmartshelfConfig.model.feature_names_in_)
                if os.path.exists(fill_values_path):
                    ProjectsmartshelfConfig.fill_values = joblib.load(fill_values_path)
                print(f"ML model loaded successfully from {os.path.basename(selected_model_path)}.")
            else:
                print("ML files not found. Check paths.")

        if is_runserver or not settings.DEBUG:
            def sync_prices():
                from .utils import send_all_prices_to_displays
                send_all_prices_to_displays()
            threading.Thread(target=sync_prices, daemon=True).start()
