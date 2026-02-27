from django.db import models
from django.utils import timezone
import datetime
from decimal import Decimal


class DailyProductSales(models.Model):
    """
    Server-side analytics table that aggregates daily sales per product
    """

    date = models.DateField(
        verbose_name="Sales Date", help_text="Date of the sales summary"
    )

    day_of_week = models.CharField(
        max_length=20,
        verbose_name="Day of Week",
        help_text="Day of the week for the sales",
    )

    category = models.CharField(
        max_length=100,
        verbose_name="Product Category",
        help_text="Category of the product",
    )

    product_name = models.CharField(
        max_length=200, verbose_name="Product Name", help_text="Name of the product"
    )

    current_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Current Price",
        help_text="Price of the product on this date",
    )

    units_sold = models.PositiveIntegerField(
        verbose_name="Units Sold",
        default=0,
        help_text="Total number of units sold on this date",
    )

    total_revenue = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Total Revenue",
        default=0.00,
        help_text="Total revenue generated (units_sold × current_price)",
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'daily_product_sales'
        verbose_name = 'Daily Product Sales'
        verbose_name_plural = 'Daily Product Sales'
        ordering = ['-date', 'category', 'product_name']
        unique_together = ['date', 'product_name']  # One record per product per day
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['category']),
            models.Index(fields=['product_name']),
            models.Index(fields=['day_of_week']),
        ]

    def save(self, *args, **kwargs):
        if self.units_sold and self.current_price:
            self.total_revenue = self.current_price * self.units_sold
        if not self.day_of_week and self.date:
            self.day_of_week = self.date.strftime('%A')
        super().save(*args, **kwargs)

    @classmethod
    def generate_daily_summary(cls, target_date=None):
        """
        Generate daily summary for ALL products on a specific date.
        Products with no sales get units_sold = 0.
        """
        from checkout.models import LiveTransaction, Product
        import datetime

        if target_date is None:
            target_date = datetime.date.today() - datetime.timedelta(days=1)

        print(f"Generating daily summary for {target_date}...")
        
        # Get all products
        products = Product.objects.all()
        created_records = []

        for product in products:
            # Sum quantities for this product on target_date
            total_quantity = LiveTransaction.objects.filter(
                date=target_date,
                product=product
            ).aggregate(total=models.Sum('quantity'))['total'] or 0

            # Use the product's current price (assumes price didn't change during the day)
            price = product.price

            # Create or update the daily sales record
            daily_sale, created = cls.objects.update_or_create(
                date=target_date,
                product_name=product.name,
                defaults={
                    'day_of_week': target_date.strftime('%A'),
                    'category': product.category,
                    'current_price': price,
                    'units_sold': total_quantity,
                }
            )
            created_records.append(daily_sale)
            print(f"  - {product.name}: {total_quantity} units @ ${price}")

        print(f"Generated {len(created_records)} daily summaries for {target_date}")
        return created_records

    @classmethod
    def generate_summary_for_date_range(cls, start_date, end_date):
        """Generate summaries for a range of dates"""
        current_date = start_date
        all_records = []

        while current_date <= end_date:
            records = cls.generate_daily_summary(current_date)
            all_records.extend(records)
            current_date += datetime.timedelta(days=1)

        return all_records
