import os
import joblib
from django.conf import settings
from django.apps import AppConfig


class ProjectsmartshelfConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ProjectSmartShelf'

    # Placeholders
    model = None
    cat_encoder = None
    prod_encoder = None

    def ready(self):
        # paths to your files
        model_path = os.path.join(settings.BASE_DIR, 'Projectsmartshelf/ml_models/price_prediction_model.pkl')
        cat_path = os.path.join(settings.BASE_DIR, 'Projectsmartshelf/ml_models/category_encoder.pkl')
        prod_path = os.path.join(settings.BASE_DIR, 'Projectsmartshelf/ml_models/product_encoder.pkl')

        # Load them
        if os.path.exists(model_path):
            ProjectsmartshelfConfig.model = joblib.load(model_path)
            ProjectsmartshelfConfig.cat_encoder = joblib.load(cat_path)
            ProjectsmartshelfConfig.prod_encoder = joblib.load(prod_path)
        else:
            print("ML files not found. Check paths.")