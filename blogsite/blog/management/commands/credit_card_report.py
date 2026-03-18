from datetime import datetime
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ...credit_card_report import CreditCardReportError
from ...credit_card_report import generate_daily_report


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
            report, json_path, text_path = generate_daily_report(report_date)
        except CreditCardReportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"report_date={report.report_date}")
        self.stdout.write(f"checked_messages={report.checked_message_count}")
        self.stdout.write(f"matched_transactions={report.matched_message_count}")
        self.stdout.write(f"total_amount={report.total_amount}")
        self.stdout.write(f"json_report={json_path}")
        self.stdout.write(f"text_report={text_path}")
        self.stdout.write(f"timezone={settings.CREDIT_CARD_REPORT_TIME_ZONE}")
