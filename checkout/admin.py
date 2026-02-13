from django.contrib import admin
from .models import Product, LiveTransaction

# Register your models here.

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price']
    list_editable = ['price']


@admin.register(LiveTransaction)
class LiveTransactionAdmin(admin.ModelAdmin):
    list_display = ['transaction_id', 'date', 'time', 'product', 'quantity', 'total_amount']
    readonly_fields = ['date', 'time', 'day_of_week', 'total_amount', 'unit_price', 'category']
