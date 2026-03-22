from django.urls import path
from django.contrib.auth import views as auth_views
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
]
