from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.conf import settings
from django.utils import timezone
import datetime
from decimal import Decimal
from checkout.models import Product

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
    
class ProductLCDMapping(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name='lcd_mapping')
    i2c_address = models.CharField(max_length=10, help_text="e.g., 0x23")

    def __str__(self):
        return f"{self.product.name} -> {self.i2c_address}"


class PriceHistory(models.Model):
    """Model to track changes to product prices."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_history')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.product.name} - {self.changed_at.strftime('%Y-%m-%d %H:%M')} by {self.changed_by.username if self.changed_by else 'System'}"

    class Meta:
        ordering = ['-changed_at', '-id']  # Show newest changes first


class ScheduledPromotion(models.Model):
    """Date-based promotion that automatically applies and reverts product prices."""

    STATUS_SCHEDULED = 'scheduled'
    STATUS_ACTIVE = 'active'
    STATUS_EXPIRED = 'expired'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_EXPIRED, 'Expired'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='scheduled_promotions',
    )
    title = models.CharField(max_length=120, blank=True)
    promotional_price = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField()
    end_time = models.TimeField(null=True, blank=True)
    pre_promotion_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Price captured immediately before the promotion activates.',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SCHEDULED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    reverted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', 'start_date', 'start_time', 'product__name']
        indexes = [
            models.Index(fields=['status', 'start_date', 'start_time', 'end_date', 'end_time'], name='promo_schedule_idx'),
            models.Index(fields=['product', 'status'], name='promo_product_status_idx'),
        ]

    def __str__(self):
        label = self.title or f"{self.product.name} promotion"
        return f"{label} ({self.starts_at} to {self.ends_at})"

    @property
    def display_name(self):
        return self.title or f"{self.product.name} promotion"

    @property
    def is_live(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def starts_at(self):
        return self._combine_schedule(self.start_date, self.start_time, is_end=False)

    @property
    def ends_at(self):
        return self._combine_schedule(self.end_date, self.end_time, is_end=True)

    @property
    def is_timed(self):
        return self.start_time is not None or self.end_time is not None

    @property
    def base_price(self):
        return self.pre_promotion_price if self.pre_promotion_price is not None else self.product.price

    def clean(self):
        if self.start_date and self.end_date and self.ends_at < self.starts_at:
            raise ValidationError({'end_date': 'End must be on or after the start.'})
        if self.promotional_price is not None and self.promotional_price < Decimal('0.00'):
            raise ValidationError({'promotional_price': 'Promotional price cannot be negative.'})
        if (self.start_time and not self.end_time) or (self.end_time and not self.start_time):
            raise ValidationError({'end_time': 'Timed promotions need both start and end times.'})

    def _write_price_history(self, old_price, new_price, reason):
        if old_price == new_price:
            return
        PriceHistory.objects.create(
            product=self.product,
            changed_by=None,
            old_price=old_price,
            new_price=new_price,
            reason=reason,
        )

    def _sync_lcd(self, promotion_label=None):
        if not self.product.lcd_id or self.product.lcd_id < 1:
            return False
        from .utils import send_price_update

        if promotion_label:
            return send_price_update(
                self.product.lcd_id,
                self.product.name,
                str(self.product.price),
                promotion_label=promotion_label,
            )
        return send_price_update(self.product.lcd_id, self.product.name, str(self.product.price))

    def activate(self, now=None):
        if self.status != self.STATUS_SCHEDULED:
            return False

        now = now or timezone.now()
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=self.product_id)
            self.product = product
            old_price = product.price
            product.price = self.promotional_price
            product.save(update_fields=['price'])

            self.pre_promotion_price = old_price
            self.status = self.STATUS_ACTIVE
            self.applied_at = now
            self.save(update_fields=['pre_promotion_price', 'status', 'applied_at', 'updated_at'])
            self._write_price_history(
                old_price,
                self.promotional_price,
                f"Promotion started: {self.display_name}",
            )

        self._sync_lcd(promotion_label='PROMO')
        return True

    def revert(self, final_status=None, now=None):
        if self.status != self.STATUS_ACTIVE:
            return False

        now = now or timezone.now()
        final_status = final_status or self.STATUS_EXPIRED
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=self.product_id)
            self.product = product
            old_price = product.price
            restored_price = (
                self.pre_promotion_price
                if self.pre_promotion_price is not None
                else product.price
            )
            product.price = restored_price
            product.save(update_fields=['price'])

            self.status = final_status
            self.reverted_at = now
            self.save(update_fields=['status', 'reverted_at', 'updated_at'])
            reason = (
                f"Promotion cancelled: {self.display_name}"
                if final_status == self.STATUS_CANCELLED
                else f"Promotion ended: {self.display_name}"
            )
            self._write_price_history(old_price, restored_price, reason)

        self._sync_lcd()
        return True

    def cancel(self, now=None):
        if self.status == self.STATUS_ACTIVE:
            return self.revert(final_status=self.STATUS_CANCELLED, now=now)
        if self.status == self.STATUS_SCHEDULED:
            self.status = self.STATUS_CANCELLED
            self.reverted_at = now or timezone.now()
            self.save(update_fields=['status', 'reverted_at', 'updated_at'])
            return True
        return False

    @classmethod
    def _combine_schedule(cls, schedule_date, schedule_time=None, is_end=False):
        if not schedule_date:
            return None
        schedule_time = schedule_time or (datetime.time.max if is_end else datetime.time.min)
        value = datetime.datetime.combine(schedule_date, schedule_time)
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value

    @classmethod
    def _coerce_effective_at(cls, value=None):
        if value is None:
            return timezone.localtime()
        if isinstance(value, datetime.datetime):
            if timezone.is_naive(value):
                return timezone.make_aware(value, timezone.get_current_timezone())
            return timezone.localtime(value)
        if isinstance(value, datetime.date):
            return cls._combine_schedule(value, datetime.time.min)
        return value

    @classmethod
    def overlapping_for(cls, product, start_date, end_date, start_time=None, end_time=None):
        start_at = cls._combine_schedule(start_date, start_time, is_end=False)
        end_at = cls._combine_schedule(end_date, end_time, is_end=True)
        candidates = cls.objects.filter(
            product=product,
            status__in=[cls.STATUS_SCHEDULED, cls.STATUS_ACTIVE],
            start_date__lte=end_date,
            end_date__gte=start_date,
        )
        return [
            promotion for promotion in candidates
            if promotion.starts_at <= end_at and promotion.ends_at >= start_at
        ]

    @classmethod
    def process_due_promotions(cls, effective_at=None):
        """Activate, expire, and revert promotions for the given operational time."""
        effective_at = cls._coerce_effective_at(effective_at)
        effective_date = effective_at.date()
        now = timezone.now()
        results = {
            'activated': 0,
            'reverted': 0,
            'expired_without_activation': 0,
        }

        for promotion in cls.objects.select_related('product').filter(
            status=cls.STATUS_ACTIVE,
            end_date__lte=effective_date,
        ):
            if promotion.ends_at < effective_at and promotion.revert(now=now):
                results['reverted'] += 1

        overdue_promotions = cls.objects.filter(
            status=cls.STATUS_SCHEDULED,
            end_date__lte=effective_date,
        )
        for promotion in overdue_promotions:
            if promotion.ends_at < effective_at:
                promotion.status = cls.STATUS_EXPIRED
                promotion.reverted_at = now
                promotion.save(update_fields=['status', 'reverted_at', 'updated_at'])
                results['expired_without_activation'] += 1

        due_promotions = cls.objects.select_related('product').filter(
            status=cls.STATUS_SCHEDULED,
            start_date__lte=effective_date,
            end_date__gte=effective_date,
        ).order_by('start_date', 'start_time', 'created_at')
        for promotion in due_promotions:
            if promotion.starts_at <= effective_at <= promotion.ends_at and promotion.activate(now=now):
                results['activated'] += 1

        return results


class SimulationState(models.Model):
    """Shared operational date used by dashboard and checkout sessions."""

    key = models.CharField(max_length=32, unique=True, default='active_day')
    current_date = models.DateField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_state'

    def __str__(self):
        return f"{self.key}: {self.current_date}"
