from django.contrib import admin
from .models import DailyProductSales, ProductLCDMapping, ScheduledPromotion
import datetime

@admin.register(DailyProductSales)
class DailyProductSalesAdmin(admin.ModelAdmin):
    list_display = [
        "date",
        "product_name",
        "category",
        "units_sold",
        "current_price",
        "total_revenue",
    ]
    list_filter = ["date", "category", "day_of_week"]
    search_fields = ["product_name", "category"]
    readonly_fields = ["total_revenue"]
    list_editable = ["units_sold", "current_price"]

    fieldsets = (
        (
            "Basic Information",
            {"fields": ("date", "day_of_week", "category", "product_name")},
        ),
        ("Sales Data", {"fields": ("current_price", "units_sold", "total_revenue")}),
    )

    actions = ["generate_yesterday_summary", "generate_today_summary"]

    def generate_yesterday_summary(self, request, queryset):
        """Admin action to generate yesterday's summary"""
        from django.utils import timezone

        yesterday = timezone.now().date() - datetime.timedelta(days=1)
        records = DailyProductSales.generate_daily_summary(yesterday)
        self.message_user(
            request, f"Generated {len(records)} summaries for {yesterday}"
        )

    def generate_today_summary(self, request, queryset):
        """Admin action to generate today's summary"""
        from django.utils import timezone

        today = timezone.now().date()
        records = DailyProductSales.generate_daily_summary(today)
        self.message_user(request, f"Generated {len(records)} summaries for {today}")

    generate_yesterday_summary.short_description = "Generate yesterday's sales summary"
    generate_today_summary.short_description = "Generate today's sales summary (so far)"

@admin.register(ProductLCDMapping)
class ProductLCDMappingAdmin(admin.ModelAdmin):
    list_display = ['product', 'i2c_address']
    search_fields = ['product__name', 'i2c_address']


@admin.register(ScheduledPromotion)
class ScheduledPromotionAdmin(admin.ModelAdmin):
    list_display = [
        'display_name',
        'product',
        'status',
        'promotional_price',
        'start_date',
        'start_time',
        'end_date',
        'end_time',
        'pre_promotion_price',
    ]
    list_filter = ['status', 'start_date', 'end_date', 'start_time', 'end_time']
    search_fields = ['title', 'product__name']
    readonly_fields = ['pre_promotion_price', 'applied_at', 'reverted_at', 'created_at', 'updated_at']
    actions = ['process_due_promotions']

    def process_due_promotions(self, request, queryset):
        from django.utils import timezone

        results = ScheduledPromotion.process_due_promotions(timezone.localdate())
        self.message_user(
            request,
            (
                f"Processed promotions: {results['activated']} activated, "
                f"{results['reverted']} reverted."
            ),
        )

    process_due_promotions.short_description = "Process due promotions"
