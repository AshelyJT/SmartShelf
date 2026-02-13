from django.urls import path
from . import views

# URL Config
urlpatterns = [
    path("home", views.checkout, name="checkout_home"),
    path("process-transaction/", views.process_transaction, name="process_transaction"),
    path("get-products/", views.get_products, name="get_products"),
    path("recent-transactions/", views.recent_transactions, name="recent_transactions"),
]
