import pandas as pd
import datetime
import numpy as np
from ProjectSmartShelf.apps import ProjectsmartshelfConfig
from ProjectSmartShelf.models import DailyProductSales
from checkout.models import Product


def calculate_demand_change(sales_lag_1, sales_lag_2):
    if sales_lag_2 == 0:
        return 0
    return ((sales_lag_1 - sales_lag_2) / sales_lag_2) * 100


def prepare_input_for_tomorrow(product_name, category, current_inventory, expiry_date,
                               sales_yesterday, sales_day_before_yesterday, model):
   
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
    try:
        encoded_product = ProjectsmartshelfConfig.prod_encoder.transform([product_name])[0]
    except ValueError:
        encoded_product = -1
       
    try:
        encoded_category = ProjectsmartshelfConfig.cat_encoder.transform([category])[0]
    except ValueError:
        encoded_category = -1

    # 4. Dictionary of ALL possible features
    data_dict = {
        "product_name": encoded_product,
        "category": encoded_category,
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

    # 5. AUTO-ALIGNMENT (The Magic Fix)
    # We ask the model: "What feature names do you have?"
    try:
        # Get the feature names directly from the trained model
        expected_cols = model.feature_names_in_
       
        # Reorder the DataFrame to match the model exactly
        df = df[expected_cols]
       
    except AttributeError:
        # Fallback if the model doesn't support feature_names_in_
        print("Warning: Could not auto-align features. Using default order.")

    return df

def prepare_advanced_input(product_name, category, current_inventory, days_until_expiry,
                           sales_lag_1, sales_lag_2, demand_change, prediction_date,
                           model):
    if isinstance(prediction_date, str):
        prediction_date = datetime.datetime.strptime(prediction_date, "%Y-%m-%d").date()

    try:
        encoded_product = ProjectsmartshelfConfig.prod_encoder.transform([product_name])[0]
    except ValueError:
        encoded_product = -1

    try:
        encoded_category = ProjectsmartshelfConfig.cat_encoder.transform([category])[0]
    except ValueError:
        encoded_category = -1

    data_dict = {
        "product_name": encoded_product,
        "category": encoded_category,
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

    try:
        df = df[model.feature_names_in_]
    except AttributeError:
        print("Warning: Could not auto-align features. Using default order.")

    return df

def predict_prices_batch(base_date=None):
    """
    Generates AI price recommendations for ALL products in the database.
    If base_date is provided, it uses that as the "current" day.
    Otherwise, uses today's real date.
    """
    model = ProjectsmartshelfConfig.model
    
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
        try:
            enc_prod = ProjectsmartshelfConfig.prod_encoder.transform([p.name])[0]
        except ValueError:
            enc_prod = -1
           
        try:
            enc_cat = ProjectsmartshelfConfig.cat_encoder.transform([p.category])[0]
        except ValueError:
            enc_cat = -1

        # Days until expiry (relative to tomorrow)
        if p.expiry_date:
            days_until_expiry = (p.expiry_date - tomorrow).days
        else:
            days_until_expiry = 30

        row = {
            "product_name": enc_prod,
            "category": enc_cat,
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

    if model:
        try:
            df = df[model.feature_names_in_]
        except AttributeError:
            pass
           
        raw_predictions = model.predict(df)
        clean_prices = [round(float(price), 2) for price in raw_predictions]
        results = zip(valid_products, clean_prices)
        return list(results)

    return []
