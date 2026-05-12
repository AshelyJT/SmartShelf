from django.urls import path
from . import views

urlpatterns = [
    path("staff-dashboard/", views.analytics_dashboard, name="staff_dashboard"),
    path("api/generate-summary/", views.generate_summary_api, name="generate_summary"),
    path("api/sales-report/", views.sales_report_api, name="sales_report"),
    path(
        "api/product-trend/<int:product_id>/",
        views.product_trend_api,
        name="product_trend",
    ),
    path("api/product-trend/", views.product_trend_api, name="product_trend_by_name"),
    path("predict/", views.predict_tomorrow_price, name="predict_price"),
    path("pricing-dashboard/", views.pricing_dashboard, name="pricing_dashboard"),
    path("api/close-day-advance/", views.close_day_advance, name="close_day_advance"),
    path("apply-single-price/", views.apply_single_price, name="apply_single_price"),
    path("predict_tomorrow_price/", views.predict_tomorrow_price, name="predict_tomorrow_price"),
    path("recommendations/", views.recommendations_page, name="recommendations"),
    path("price-history/", views.price_history_view, name="price_history"),
    path("price-history/export/", views.export_price_history_csv, name="export_price_history_csv"),
    path("manual-pricing/", views.manual_pricing_view, name="manual_pricing"),
    path("promotions/", views.promotions_view, name="promotions"),
    path("inventory-management/", views.inventory_management_view, name="inventory_management"),
    path("lcd-management/", views.lcd_management_view, name="lcd_management"),
]
