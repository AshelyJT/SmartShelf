import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ProjectSmartShelf.models import ScheduledPromotion


class Command(BaseCommand):
    help = "Activate due scheduled promotions and revert expired promotions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Operational date to process (YYYY-MM-DD). Default is today.",
        )
        parser.add_argument(
            "--datetime",
            type=str,
            help="Operational timestamp to process (YYYY-MM-DD HH:MM). Overrides --date.",
        )

    def handle(self, *args, **options):
        if options["datetime"]:
            effective_at = parse_datetime(options["datetime"])
            if effective_at is None:
                try:
                    effective_at = datetime.datetime.strptime(options["datetime"], "%Y-%m-%d %H:%M")
                except ValueError:
                    self.stdout.write(self.style.ERROR("Invalid datetime format. Use YYYY-MM-DD HH:MM"))
                    return
        elif options["date"]:
            try:
                effective_at = datetime.datetime.strptime(
                    options["date"], "%Y-%m-%d"
                )
            except ValueError:
                self.stdout.write(self.style.ERROR("Invalid date format. Use YYYY-MM-DD"))
                return
        else:
            effective_at = timezone.localtime()

        results = ScheduledPromotion.process_due_promotions(effective_at)
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Processed promotions for {ScheduledPromotion._coerce_effective_at(effective_at):%Y-%m-%d %H:%M}: "
                    f"{results['activated']} activated, "
                    f"{results['reverted']} reverted, "
                    f"{results['expired_without_activation']} expired without activation."
                )
            )
        )
