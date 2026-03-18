from datetime import date
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .credit_card_report import build_report
from .credit_card_report import parse_credit_card_transactions
from .credit_card_report import parse_credit_card_transaction
from .credit_card_report import render_text_report
from .credit_card_report import save_report


class CreditCardReportTests(SimpleTestCase):
    def test_parse_credit_card_transaction_from_email(self):
        message = EmailMessage()
        message["Subject"] = "招商银行信用卡交易提醒"
        message["From"] = "招商银行 <service@cmbchina.com>"
        message["Date"] = "Thu, 19 Mar 2026 09:15:00 +0800"
        message.set_content(
            "\n".join(
                [
                    "您尾号1234的信用卡发生交易。",
                    "交易金额：人民币88.50",
                    "商户：瑞幸咖啡深圳南山店",
                    "交易提醒仅供参考。",
                ]
            )
        )

        transaction = parse_credit_card_transaction(
            message.as_bytes(),
            target_date=date(2026, 3, 19),
            tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.amount, "88.50")
        self.assertEqual(transaction.category, "餐饮")
        self.assertIn("瑞幸咖啡", transaction.merchant)

    def test_bill_notice_is_not_treated_as_spending(self):
        message = EmailMessage()
        message["Subject"] = "招商银行信用卡账单提醒"
        message["From"] = "招商银行 <service@cmbchina.com>"
        message["Date"] = "Thu, 19 Mar 2026 11:20:00 +0800"
        message.set_content("本期账单金额：人民币888.00，请及时还款。")

        transaction = parse_credit_card_transaction(
            message.as_bytes(),
            target_date=date(2026, 3, 19),
            tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertIsNone(transaction)

    def test_parse_daily_digest_into_multiple_transactions(self):
        message = EmailMessage()
        message["Subject"] = "每日信用管家"
        message["From"] = "招商银行信用卡 <ccsvc@message.cmbchina.com>"
        message["Date"] = "Wed, 18 Mar 2026 13:27:36 +0800"
        message.set_content(
            "\n".join(
                [
                    "截至昨日最后一笔交易，您的额度和积分信息如下：",
                    "2026/03/17 您的消费明细如下：",
                    "02:37:10",
                    "CNY 91.11",
                    "尾号4457 消费 支付宝-上海拉扎斯信息科技有限公司",
                    "18:12:41",
                    "CNY 22.56",
                    "尾号4457 消费 支付宝-碗大厨社区厨房",
                    "18:12:56",
                    "CNY 23.46",
                    "尾号4457 消费 支付宝-碗大厨社区厨房",
                ]
            )
        )

        transactions = parse_credit_card_transactions(
            message.as_bytes(),
            target_date=date(2026, 3, 17),
            tz=ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(len(transactions), 3)
        self.assertEqual(transactions[0].amount, "91.11")
        self.assertEqual(transactions[1].merchant, "支付宝-碗大厨社区厨房")
        self.assertEqual(transactions[0].category, "餐饮")
        self.assertEqual(transactions[1].category, "餐饮")

    def test_save_report_writes_json_and_text_files(self):
        tz = ZoneInfo("Asia/Shanghai")
        report = build_report(
            target_date=date(2026, 3, 19),
            tz=tz,
            checked_count=3,
            transactions=[
                parse_credit_card_transaction(
                    self._build_mail(
                        subject="中国银行信用卡消费提醒",
                        sender="中国银行 <service@boc.cn>",
                        body="\n".join(
                            [
                                "尾号8888信用卡本次消费。",
                                "消费金额：人民币45.00",
                                "商户：滴滴出行",
                            ]
                        ),
                    ),
                    target_date=date(2026, 3, 19),
                    tz=tz,
                ),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            json_path, text_path = save_report(report, Path(temp_dir))

            self.assertTrue(json_path.exists())
            self.assertTrue(text_path.exists())
            self.assertIn("总消费: 45.00 元", text_path.read_text(encoding="utf-8"))

    def test_render_text_report_handles_empty_day(self):
        report = build_report(
            target_date=date(2026, 3, 19),
            tz=ZoneInfo("Asia/Shanghai"),
            checked_count=0,
            transactions=[],
        )

        text = render_text_report(report)
        self.assertIn("今天未识别到信用卡消费邮件", text)

    def _build_mail(self, subject, sender, body):
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["Date"] = datetime(2026, 3, 19, 13, 5, tzinfo=ZoneInfo("Asia/Shanghai")).strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )
        message.set_content(body)
        return message.as_bytes()
