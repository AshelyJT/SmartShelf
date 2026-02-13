# analytics/management/commands/generate_daily_summary.py
from django.core.management.base import BaseCommand
from django.utils import timezone
import datetime
from ProjectSmartShelf.models import DailyProductSales


class Command(BaseCommand):
    help = "Generate daily sales summary for products"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Date to generate summary for (YYYY-MM-DD). Default is yesterday.",
        )
        parser.add_argument(
            "--all-dates",
            action="store_true",
            help="Generate summaries for all dates with transactions",
        )
        parser.add_argument(
            "--range",
            type=str,
            help="Date range to generate summaries for (YYYY-MM-DD:YYYY-MM-DD)",
        )

    def handle(self, *args, **options):
        if options["all_dates"]:
            # Get all unique dates from transactions
            from checkout.models import LiveTransaction

            dates = LiveTransaction.objects.dates("date", "day")

            for date in dates:
                records = DailyProductSales.generate_daily_summary(date)
                self.stdout.write(
                    self.style.SUCCESS(f"Generated {len(records)} summaries for {date}")
                )

        elif options["range"]:
            # Parse date range
            try:
                start_str, end_str = options["range"].split(":")
                start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
                end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()

                records = DailyProductSales.generate_summary_for_date_range(
                    start_date, end_date
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Generated {len(records)} summaries from {start_date} to {end_date}"
                    )
                )
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(
                        "Invalid date range format. Use YYYY-MM-DD:YYYY-MM-DD"
                    )
                )

        else:
            # Single date
            if options["date"]:
                try:
                    target_date = datetime.datetime.strptime(
                        options["date"], "%Y-%m-%d"
                    ).date()
                except ValueError:
                    self.stdout.write(
                        self.style.ERROR("Invalid date format. Use YYYY-MM-DD")
                    )
                    return
            else:
                # Default to yesterday
                target_date = timezone.now().date() - datetime.timedelta(days=1)

            records = DailyProductSales.generate_daily_summary(target_date)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Generated {len(records)} summaries for {target_date}"
                )
            )
