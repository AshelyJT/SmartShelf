import pandas as pd
import datetime
import numpy as np
from ProjectSmartShelf.apps import ProjectsmartshelfConfig
from ProjectSmartShelf.models import DailyProductSales
from checkout.models import Product

def prepare_input_for_tomorrow(product_name, category, current_inventory, expiry_date,
                               sales_today, sales_yesterday, model):
   
    # 1. Date Calculations
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    if isinstance(expiry_date, str):
        expiry_date = datetime.datetime.strptime(expiry_date, "%Y-%m-%d").date()
    days_until_expiry = (expiry_date - tomorrow).days

    # 2. Lags & Demand Change
    sales_lag_1 = sales_today
    sales_lag_2 = sales_yesterday
   
    if sales_lag_2 == 0:
        demand_change = 0
    else:
        demand_change = ((sales_lag_1 - sales_lag_2) / sales_lag_2) * 100

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

def predict_prices_batch():
    """
    Generates AI price recommendations for ALL products in the database.
    """
    model = ProjectsmartshelfConfig.model
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    tomorrow = today + datetime.timedelta(days=1)

    # 1. OPTIMIZATION: Fetch Sales History in Bulk (2 Queries only)
    # create a dictionary: {'Milk': 50, 'Bread': 12, ...}
    sales_today_map = {
        item.product_name: item.units_sold
        for item in DailyProductSales.objects.filter(date=today)
    }
   
    sales_yesterday_map = {
        item.product_name: item.units_sold
        for item in DailyProductSales.objects.filter(date=yesterday)
    }

    # 2. Get All Products (assuming Product model has expiry/inventory)
    products = Product.objects.all()
    data_list = []
    valid_products = [] # Keep track of which product matches which row

    for p in products:
        # A. Look up sales from our dictionaries (Fast!)
        s_today = sales_today_map.get(p.name, 0)      # Default to 0 if no record
        s_yesterday = sales_yesterday_map.get(p.name, 0) # Default to 0 if no record

        # B. Calculate Demand Change
        if s_yesterday == 0:
            demand_change = 0
        else:
            demand_change = ((s_today - s_yesterday) / s_yesterday) * 100

        # C. Encode Categorical Data
        try:
            enc_prod = ProjectsmartshelfConfig.prod_encoder.transform([p.name])[0]
        except ValueError:
            enc_prod = -1 # Unknown product
           
        try:
            enc_cat = ProjectsmartshelfConfig.cat_encoder.transform([p.category])[0]
        except ValueError:
            enc_cat = -1 # Unknown category

        # D. Date Math
        if p.expiry_date:
            days_until_expiry = (p.expiry_date - tomorrow).days
        else:
            # Fallback: If no date is set, assume it lasts 30 days (or any default you prefer)
            days_until_expiry = 30

        # E. Build Row
        row = {
            "product_name": enc_prod,
            "category": enc_cat,
            "inventory_level": p.inventory_level,
            "days_until_expiry": days_until_expiry,
            "sales_lag_1": s_today,
            "sales_lag_2": s_yesterday,
            "demand_change": demand_change,
            "year": tomorrow.year,
            "month": tomorrow.month,
            "day": tomorrow.day,
            "day_of_week": tomorrow.weekday(),
            "is_weekend": 1 if tomorrow.weekday() >= 5 else 0
        }
       
        data_list.append(row)
        valid_products.append(p)

    # 3. Create DataFrame
    if not data_list:
        return []

    df = pd.DataFrame(data_list)

    # 4. Predict
    if model:
        # Align columns
        try:
            df = df[model.feature_names_in_]
        except AttributeError:
            pass # Use default order if specific feature names aren't saved
           
        raw_predictions = model.predict(df)
       
        # 5. Combine Product + Prediction
        # Returns a list of tuples: [(ProductObj, 12.50), (ProductObj, 10.00)]
        clean_prices = [round(float(price),2) for price in raw_predictions]
        results = zip(valid_products, clean_prices)
        return list(results)

    return []