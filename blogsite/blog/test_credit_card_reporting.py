from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.test import override_settings

from .credit_card_reporting import build_period_summary
from .credit_card_reporting import resolve_feishu_report_targets
from .credit_card_reporting import sync_daily_snapshot
from .models import CreditCardDailySnapshot
from .models import CreditCardTransactionRecord
from .models import FeishuChatSession


class CreditCardReportingTests(TestCase):
    @patch("blog.credit_card_reporting.generate_daily_report")
    def test_sync_daily_snapshot_persists_report_and_transactions(self, generate_daily_report_mock):
        generate_daily_report_mock.return_value = (
            SimpleNamespace(
                generated_at="2026-03-19T23:30:00+08:00",
                checked_message_count=6,
                matched_message_count=2,
                total_amount="123.45",
                category_totals=[
                    {"category": "餐饮", "amount": "100.00", "count": 1},
                    {"category": "出行", "amount": "23.45", "count": 1},
                ],
                transactions=[
                    {
                        "received_at": "2026-03-18T09:30:00+08:00",
                        "amount": "100.00",
                        "merchant": "美团外卖",
                        "category": "餐饮",
                        "subject": "每日信用管家",
                        "sender": "招商银行信用卡 <ccsvc@message.cmbchina.com>",
                        "snippet": "尾号4457 消费 美团外卖",
                    },
                    {
                        "received_at": "2026-03-18T18:40:00+08:00",
                        "amount": "23.45",
                        "merchant": "滴滴出行",
                        "category": "出行",
                        "subject": "每日信用管家",
                        "sender": "招商银行信用卡 <ccsvc@message.cmbchina.com>",
                        "snippet": "尾号4457 消费 滴滴出行",
                    },
                ],
            ),
            Path("/app/data/credit_card_reports/2026-03-18.json"),
            Path("/app/data/credit_card_reports/2026-03-18.txt"),
        )

        snapshot = sync_daily_snapshot(date(2026, 3, 18))

        self.assertEqual(snapshot.report_date.isoformat(), "2026-03-18")
        self.assertEqual(snapshot.checked_message_count, 6)
        self.assertEqual(snapshot.matched_transaction_count, 2)
        self.assertEqual(snapshot.total_amount, Decimal("123.45"))
        self.assertEqual(snapshot.transactions.count(), 2)
        self.assertEqual(snapshot.transactions.first().merchant, "美团外卖")

    @override_settings(
        CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID="oc_report_chat",
        CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID_TYPE="chat_id",
    )
    def test_resolve_feishu_targets_prefers_explicit_config(self):
        FeishuChatSession.objects.create(chat_id="oc_old", user_open_id="ou_old")

        self.assertEqual(
            resolve_feishu_report_targets(),
            [("oc_report_chat", "chat_id")],
        )

    @override_settings(
        CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID="",
        CREDIT_CARD_REPORT_FEISHU_USE_LATEST_SESSION=True,
    )
    def test_resolve_feishu_targets_falls_back_to_latest_session(self):
        FeishuChatSession.objects.create(chat_id="oc_1", user_open_id="ou_1")
        latest = FeishuChatSession.objects.create(chat_id="oc_2", user_open_id="ou_2")

        self.assertEqual(
            resolve_feishu_report_targets(),
            [(latest.chat_id, "chat_id")],
        )

    def test_build_period_summary_aggregates_snapshots(self):
        snapshot_a = CreditCardDailySnapshot.objects.create(
            report_date=date(2026, 3, 17),
            checked_message_count=5,
            matched_transaction_count=2,
            total_amount=Decimal("60.00"),
            category_totals=[{"category": "餐饮", "amount": "60.00", "count": 2}],
            generated_at="2026-03-18T23:30:00+08:00",
        )
        snapshot_b = CreditCardDailySnapshot.objects.create(
            report_date=date(2026, 3, 18),
            checked_message_count=4,
            matched_transaction_count=1,
            total_amount=Decimal("40.00"),
            category_totals=[{"category": "出行", "amount": "40.00", "count": 1}],
            generated_at="2026-03-19T23:30:00+08:00",
        )
        CreditCardTransactionRecord.objects.create(
            daily_snapshot=snapshot_a,
            occurred_at="2026-03-17T09:00:00+08:00",
            amount=Decimal("20.00"),
            merchant="美团外卖",
            category="餐饮",
            subject="每日信用管家",
            sender="招商银行信用卡",
            snippet="",
        )
        CreditCardTransactionRecord.objects.create(
            daily_snapshot=snapshot_a,
            occurred_at="2026-03-17T18:00:00+08:00",
            amount=Decimal("40.00"),
            merchant="瑞幸咖啡",
            category="餐饮",
            subject="每日信用管家",
            sender="招商银行信用卡",
            snippet="",
        )
        CreditCardTransactionRecord.objects.create(
            daily_snapshot=snapshot_b,
            occurred_at="2026-03-18T08:30:00+08:00",
            amount=Decimal("40.00"),
            merchant="滴滴出行",
            category="出行",
            subject="每日信用管家",
            sender="招商银行信用卡",
            snippet="",
        )

        summary = build_period_summary(date(2026, 3, 17), date(2026, 3, 18), "weekly")

        self.assertEqual(summary["total_amount"], Decimal("100.00"))
        self.assertEqual(summary["transaction_count"], 3)
        self.assertEqual(summary["days_with_spend"], 2)
        self.assertEqual(summary["category_totals"][0]["category"], "餐饮")
        self.assertEqual(summary["top_day"]["date"].isoformat(), "2026-03-17")

    @patch("blog.management.commands.credit_card_push_reports.sync_and_push_scheduled_reports")
    def test_push_reports_command_outputs_results(self, sync_and_push_mock):
        sync_and_push_mock.return_value = [
            ("daily", date(2026, 3, 18), 1, "daily text"),
            ("weekly", date(2026, 3, 18), 1, "weekly text"),
        ]

        with patch("sys.stdout.write") as stdout_write:
            call_command(
                "credit_card_push_reports",
                "--run-date",
                "2026-03-19",
                "--force-weekly",
                "--skip-monthly",
                "--no-push",
            )

        output = "".join(call.args[0] for call in stdout_write.call_args_list)
        self.assertIn("daily_report_period_end=2026-03-18", output)
        self.assertIn("weekly_report_period_end=2026-03-18", output)
