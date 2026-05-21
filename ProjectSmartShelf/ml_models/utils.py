import pandas as pd
import datetime
from ProjectSmartShelf.apps import ProjectsmartshelfConfig
from ProjectSmartShelf.models import DailyProductSales
from checkout.models import Product


def calculate_demand_change(sales_lag_1, sales_lag_2):
    if sales_lag_2 == 0:
        return 0
    return ((sales_lag_1 - sales_lag_2) / sales_lag_2) * 100


def _as_float(value, default=0):
    if value in (None, ''):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_encode(encoder, value):
    try:
        return encoder.transform([value])[0]
    except (AttributeError, ValueError):
        return -1


def _feature_columns_for(model, df):
    model_columns = getattr(model, 'feature_names_in_', None)
    if model_columns is not None:
        return list(model_columns)
    if ProjectsmartshelfConfig.feature_columns:
        return list(ProjectsmartshelfConfig.feature_columns)
    return list(df.columns)


def _align_features(df, model):
    expected_cols = _feature_columns_for(model, df)
    fill_values = ProjectsmartshelfConfig.fill_values or {}
    for column in expected_cols:
        if column not in df.columns:
            df[column] = fill_values.get(column, 0)
    return df[expected_cols]


def prepare_input_for_tomorrow(product_name, category, current_inventory, expiry_date,
                               sales_yesterday, sales_day_before_yesterday, model,
                               current_price=0):
   
    # 1. Date Calculations
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    if isinstance(expiry_date, str):
        expiry_date = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").date()
    days_until_expiry = (expiry_date - tomorrow).days

    # 2. Lags & Demand Change
    sales_lag_1 = sales_yesterday
    sales_lag_2 = sales_day_before_yesterday
    demand_change = calculate_demand_change(sales_lag_1, sales_lag_2)

    # 3. Encoders
    encoded_product = _safe_encode(ProjectsmartshelfConfig.prod_encoder, product_name)
    encoded_category = _safe_encode(ProjectsmartshelfConfig.cat_encoder, category)

    # 4. Dictionary of ALL possible features
    data_dict = {
        "product_name": encoded_product,
        "category": encoded_category,
        "current_price": _as_float(current_price),
        "inventory_level": current_inventory,
        "days_until_expiry": days_until_expiry,
        "sales_lag_1": sales_lag_1,
        "sales_lag_2": sales_lag_2,
        "demand_change": demand_change,
        "year": tomorrow.year,
        "month": tomorrow.month,
        "day": tomorrow.day,
        "day_of_week": tomorrow.weekday(),
        "is_weekend": 1 if tomorrow.weekday() >= 5 else 0
    }

    df = pd.DataFrame([data_dict])
    return _align_features(df, model)

def prepare_advanced_input(product_name, category, current_inventory, days_until_expiry,
                           sales_lag_1, sales_lag_2, demand_change, prediction_date,
                           model, current_price=0):
    if isinstance(prediction_date, str):
        prediction_date = datetime.datetime.strptime(prediction_date, "%Y-%m-%d").date()

    encoded_product = _safe_encode(ProjectsmartshelfConfig.prod_encoder, product_name)
    encoded_category = _safe_encode(ProjectsmartshelfConfig.cat_encoder, category)

    data_dict = {
        "product_name": encoded_product,
        "category": encoded_category,
        "current_price": _as_float(current_price),
        "inventory_level": current_inventory,
        "days_until_expiry": days_until_expiry,
        "sales_lag_1": sales_lag_1,
        "sales_lag_2": sales_lag_2,
        "demand_change": demand_change,
        "year": prediction_date.year,
        "month": prediction_date.month,
        "day": prediction_date.day,
        "day_of_week": prediction_date.weekday(),
        "is_weekend": 1 if prediction_date.weekday() >= 5 else 0
    }

    df = pd.DataFrame([data_dict])
    return _align_features(df, model)

def predict_prices_batch(base_date=None):
    """
    Generates AI price recommendations for ALL products in the database.
    If base_date is provided, it uses that as the "current" day.
    Otherwise, uses today's real date.
    """
    model = ProjectsmartshelfConfig.model
    if not model:
        return []

    # Use provided base_date, or fall back to real today
    if base_date is None:
        base_date = datetime.date.today()
    
    current_date = base_date
    yesterday = current_date - datetime.timedelta(days=1)
    day_before_yesterday = current_date - datetime.timedelta(days=2)
    tomorrow = current_date + datetime.timedelta(days=1)

    # 1. Fetch Sales History in Bulk
    sales_yesterday_map = {
        item.product_name: item.units_sold
        for item in DailyProductSales.objects.filter(date=yesterday)
    }
   
    sales_day_before_yesterday_map = {
        item.product_name: item.units_sold
        for item in DailyProductSales.objects.filter(date=day_before_yesterday)
    }

    # 2. Get All Products
    products = Product.objects.all()
    data_list = []
    valid_products = []

    for p in products:
        s_yesterday = sales_yesterday_map.get(p.name, 0)
        s_day_before_yesterday = sales_day_before_yesterday_map.get(p.name, 0)

        # Demand Change
        demand_change = calculate_demand_change(s_yesterday, s_day_before_yesterday)

        # Encode categorical data
        enc_prod = _safe_encode(ProjectsmartshelfConfig.prod_encoder, p.name)
        enc_cat = _safe_encode(ProjectsmartshelfConfig.cat_encoder, p.category)

        # Days until expiry (relative to tomorrow)
        if p.expiry_date:
            days_until_expiry = (p.expiry_date - tomorrow).days
        else:
            days_until_expiry = 30

        row = {
            "product_name": enc_prod,
            "category": enc_cat,
            "current_price": _as_float(p.price),
            "inventory_level": p.inventory_level,
            "days_until_expiry": days_until_expiry,
            "sales_lag_1": s_yesterday,
            "sales_lag_2": s_day_before_yesterday,
            "demand_change": demand_change,
            "year": tomorrow.year,
            "month": tomorrow.month,
            "day": tomorrow.day,
            "day_of_week": tomorrow.weekday(),
            "is_weekend": 1 if tomorrow.weekday() >= 5 else 0
        }
       
        data_list.append(row)
        valid_products.append(p)

    if not data_list:
        return []

    df = pd.DataFrame(data_list)
    df = _align_features(df, model)
    raw_predictions = model.predict(df)
    clean_prices = [round(float(price), 2) for price in raw_predictions]
    results = zip(valid_products, clean_prices)
    return list(results)
