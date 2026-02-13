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
        db_table = "daily_product_sales"
        verbose_name = "Daily Product Sales"
        verbose_name_plural = "Daily Product Sales"
        ordering = ["-date", "category", "product_name"]
        unique_together = ["date", "product_name"]  # One record per product per day
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["category"]),
            models.Index(fields=["product_name"]),
            models.Index(fields=["day_of_week"]),
        ]

    def save(self, *args, **kwargs):
        """Auto-calculate total revenue before saving"""
        if self.units_sold and self.current_price:
            self.total_revenue = self.current_price * self.units_sold

        # Set day_of_week from date if not provided
        if not self.day_of_week and self.date:
            self.day_of_week = self.date.strftime("%A")

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} - {self.product_name} - {self.units_sold} units"

    @property
    def avg_price_per_unit(self):
        """Average price per unit (same as current_price for consistency)"""
        return self.current_price

    @classmethod
    def generate_daily_summary(cls, target_date=None):
        """
        Generate daily summary for all products for a specific date
        If no date provided, uses yesterday's date
        """
        from checkout.models import LiveTransaction, Product

        if target_date is None:
            target_date = datetime.date.today() - datetime.timedelta(days=1)

        print(f"Generating daily summary for {target_date}...")

        # Get all transactions for the target date
        transactions = LiveTransaction.objects.filter(date=target_date)

        if not transactions.exists():
            print(f"No transactions found for {target_date}")
            return []

        # Group transactions by product
        from django.db.models import Sum
        from collections import defaultdict

        # Get product info and sum quantities
        product_sales = defaultdict(
            lambda: {"units": 0, "price": None, "category": None}
        )

        for transaction in transactions:
            product_id = transaction.product_id
            product_sales[product_id]["units"] += transaction.quantity
            product_sales[product_id]["price"] = transaction.unit_price
            product_sales[product_id]["category"] = transaction.category
            product_sales[product_id]["name"] = transaction.product.name

        # Create or update DailyProductSales records
        created_records = []
        for product_id, sales_data in product_sales.items():
            # Get or create the daily summary record
            daily_sale, created = cls.objects.get_or_create(
                date=target_date,
                product_name=sales_data["name"],
                defaults={
                    "day_of_week": target_date.strftime("%A"),
                    "category": sales_data["category"],
                    "current_price": sales_data["price"],
                    "units_sold": sales_data["units"],
                },
            )

            # If record already exists, update it
            if not created:
                daily_sale.units_sold = sales_data["units"]
                daily_sale.current_price = sales_data["price"]
                daily_sale.save()

            created_records.append(daily_sale)
            print(
                f"  - {sales_data['name']}: {sales_data['units']} units @ ${sales_data['price']}"
            )

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
