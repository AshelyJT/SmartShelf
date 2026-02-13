from django.urls import path
from . import views

urlpatterns = [
    path("home/", views.home, name="pss_home"),
    path("", views.analytics_dashboard, name="dashboard"),
    path("api/generate-summary/", views.generate_summary_api, name="generate_summary"),
    path("api/sales-report/", views.sales_report_api, name="sales_report"),
    path(
        "api/product-trend/<int:product_id>/",
        views.product_trend_api,
        name="product_trend",
    ),
    path("api/product-trend/", views.product_trend_api, name="product_trend_by_name"),
    path("predict/", views.predict_tomorrow_price, name='predict_price'),
    path("pricing-dashboard/", views.pricing_dashboard, name="pricing_dashboard"),
]


