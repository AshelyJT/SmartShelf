from django.contrib import admin
from .models import DailyProductSales
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
