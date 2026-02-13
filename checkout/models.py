from django.db import models
from django.utils import timezone
import datetime
from decimal import Decimal

class Product(models.Model):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date=models.DateField(null=True, blank=True)
    inventory_level = models.IntegerField(default=0, help_text='Current stock count')
   
    def __str__(self):
        return f"{self.name} - ${self.price}"
   
    class Meta:
        ordering = ['name']


class LiveTransaction(models.Model):
    transaction_id = models.CharField(
        max_length=20,
        unique=True,
        blank=True
    )
    date = models.DateField(auto_now_add=True)
    time = models.TimeField(auto_now_add=True)
    day_of_week = models.CharField(max_length=20, blank=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True
    )
    category = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
   
    class Meta:
        db_table = 'live_trans'
        ordering = ['-created_at']
   
    def save(self, *args, **kwargs):

        # Set date/time/day_of_week
        if not self.date:
            self.date = datetime.date.today()
        if not self.time:
            self.time = datetime.datetime.now().time()
        if not self.day_of_week:
            self.day_of_week = self.date.strftime('%A')
       
        # Copy unit price and category from product
        if self.product:
            self.unit_price = self.product.price
            self.category = self.product.category
       
        # Calculate total amount
        if self.quantity and self.unit_price:
            self.total_amount = Decimal(str(self.quantity)) * self.unit_price
       
        # Generate simple transaction ID if not provided
        if not self.transaction_id:
            timestamp = datetime.datetime.now().strftime('%H%M%S')
            self.transaction_id = f"T{timestamp}"
       
        super().save(*args, **kwargs)
   
    def __str__(self):
        return f"{self.transaction_id} - {self.product.name} x{self.quantity}"