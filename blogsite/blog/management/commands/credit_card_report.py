from datetime import datetime
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ...credit_card_report import CreditCardReportError
from ...credit_card_reporting import sync_daily_snapshot


class Command(BaseCommand):
    help = "Generate a daily credit card spending report from QQ Mail."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="report_date",
            help="Target report date in YYYY-MM-DD. Defaults to today in configured timezone.",
        )
        parser.add_argument(
            "--days-ago",
            dest="days_ago",
            type=int,
            default=0,
            help="Use N days before today in the configured timezone.",
        )

    def handle(self, *args, **options):
        report_date = None
        if options.get("report_date"):
            try:
                report_date = datetime.strptime(options["report_date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--date must use YYYY-MM-DD format.") from exc
        elif options.get("days_ago"):
            report_date = (datetime.now().date() - timedelta(days=options["days_ago"]))

        try:
            snapshot = sync_daily_snapshot(report_date or datetime.now().date())
        except CreditCardReportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"report_date={snapshot.report_date.isoformat()}")
        self.stdout.write(f"checked_messages={snapshot.checked_message_count}")
        self.stdout.write(f"matched_transactions={snapshot.matched_transaction_count}")
        self.stdout.write(f"total_amount={snapshot.total_amount}")
        self.stdout.write(f"json_report={snapshot.json_report_path}")
        self.stdout.write(f"text_report={snapshot.text_report_path}")
        self.stdout.write(f"timezone={settings.CREDIT_CARD_REPORT_TIME_ZONE}")
