from datetime import datetime

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ...credit_card_report import CreditCardReportError
from ...credit_card_reporting import CreditCardPushError
from ...credit_card_reporting import sync_and_push_scheduled_reports


class Command(BaseCommand):
    help = "Sync daily credit-card data into the database and push daily/weekly/monthly reports."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-date",
            dest="run_date",
            help="Use this run date in YYYY-MM-DD instead of today.",
        )
        parser.add_argument(
            "--no-push",
            action="store_true",
            help="Generate and store reports but do not send Feishu messages.",
        )
        parser.add_argument(
            "--force-weekly",
            action="store_true",
            help="Send weekly summary regardless of weekday.",
        )
        parser.add_argument(
            "--force-monthly",
            action="store_true",
            help="Send monthly summary regardless of calendar day.",
        )
        parser.add_argument(
            "--skip-weekly",
            action="store_true",
            help="Skip weekly summary even if today is the configured weekly push day.",
        )
        parser.add_argument(
            "--skip-monthly",
            action="store_true",
            help="Skip monthly summary even if today is the configured monthly push day.",
        )

    def handle(self, *args, **options):
        run_date = datetime.now().date()
        if options.get("run_date"):
            try:
                run_date = datetime.strptime(options["run_date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--run-date must use YYYY-MM-DD format.") from exc

        include_weekly = None
        include_monthly = None
        if options["force_weekly"]:
            include_weekly = True
        elif options["skip_weekly"]:
            include_weekly = False

        if options["force_monthly"]:
            include_monthly = True
        elif options["skip_monthly"]:
            include_monthly = False

        try:
            results = sync_and_push_scheduled_reports(
                run_date,
                push=not options["no_push"],
                include_weekly=include_weekly,
                include_monthly=include_monthly,
            )
        except (CreditCardReportError, CreditCardPushError) as exc:
            raise CommandError(str(exc)) from exc

        for report_type, period_end, target_count, _ in results:
            self.stdout.write(
                f"{report_type}_report_period_end={period_end.isoformat()}"
            )
            self.stdout.write(f"{report_type}_target_count={target_count}")
