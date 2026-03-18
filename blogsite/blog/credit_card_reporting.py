from __future__ import annotations

from collections import defaultdict
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from .credit_card_report import CreditCardReportError
from .credit_card_report import generate_daily_report
from .feishu_client import FeishuConfigError
from .feishu_client import FeishuRequestError
from .feishu_client import send_text
from .models import CreditCardDailySnapshot
from .models import CreditCardTransactionRecord
from .models import FeishuChatSession


class CreditCardPushError(Exception):
    pass


def get_report_timezone():
    return ZoneInfo(settings.CREDIT_CARD_REPORT_TIME_ZONE or settings.TIME_ZONE)


def sync_daily_snapshot(report_date: date):
    report, json_path, text_path = generate_daily_report(report_date)
    generated_at = datetime.fromisoformat(report.generated_at)
    total_amount = Decimal(report.total_amount)

    with transaction.atomic():
        snapshot, _ = CreditCardDailySnapshot.objects.update_or_create(
            report_date=report_date,
            defaults={
                "checked_message_count": report.checked_message_count,
                "matched_transaction_count": report.matched_message_count,
                "total_amount": total_amount,
                "category_totals": report.category_totals,
                "generated_at": generated_at,
                "json_report_path": str(json_path),
                "text_report_path": str(text_path),
            },
        )
        snapshot.transactions.all().delete()
        CreditCardTransactionRecord.objects.bulk_create(
            [
                CreditCardTransactionRecord(
                    daily_snapshot=snapshot,
                    occurred_at=datetime.fromisoformat(item["received_at"]),
                    amount=Decimal(item["amount"]),
                    merchant=item["merchant"][:255],
                    category=item["category"][:64],
                    subject=item["subject"][:255],
                    sender=item["sender"][:255],
                    snippet=item.get("snippet", ""),
                )
                for item in report.transactions
            ]
        )

    return CreditCardDailySnapshot.objects.prefetch_related("transactions").get(pk=snapshot.pk)


def ensure_daily_snapshots(start_date: date, end_date: date):
    if end_date < start_date:
        return []

    existing = {
        item.report_date: item
        for item in CreditCardDailySnapshot.objects.filter(
            report_date__gte=start_date,
            report_date__lte=end_date,
        )
    }
    snapshots = []
    cursor = start_date
    while cursor <= end_date:
        snapshot = existing.get(cursor)
        if snapshot is None:
            snapshot = sync_daily_snapshot(cursor)
        snapshots.append(snapshot)
        cursor += timedelta(days=1)
    return snapshots


def build_period_summary(start_date: date, end_date: date, label: str):
    snapshots = list(
        CreditCardDailySnapshot.objects.filter(
            report_date__gte=start_date,
            report_date__lte=end_date,
        ).order_by("report_date")
    )
    transactions = list(
        CreditCardTransactionRecord.objects.filter(
            daily_snapshot__report_date__gte=start_date,
            daily_snapshot__report_date__lte=end_date,
        ).select_related("daily_snapshot")
    )

    total_amount = Decimal("0.00")
    transaction_count = 0
    category_totals = defaultdict(lambda: {"amount": Decimal("0.00"), "count": 0})
    merchant_totals = defaultdict(lambda: {"amount": Decimal("0.00"), "count": 0})
    daily_totals = []

    for snapshot in snapshots:
        total_amount += snapshot.total_amount
        transaction_count += snapshot.matched_transaction_count
        if snapshot.matched_transaction_count:
            daily_totals.append(
                {
                    "date": snapshot.report_date,
                    "amount": snapshot.total_amount,
                    "count": snapshot.matched_transaction_count,
                }
            )

    for item in transactions:
        category_bucket = category_totals[item.category]
        category_bucket["amount"] += item.amount
        category_bucket["count"] += 1

        merchant_bucket = merchant_totals[item.merchant]
        merchant_bucket["amount"] += item.amount
        merchant_bucket["count"] += 1

    sorted_categories = [
        {
            "category": name,
            "amount": bucket["amount"],
            "count": bucket["count"],
        }
        for name, bucket in sorted(
            category_totals.items(),
            key=lambda item: (item[1]["amount"], item[1]["count"], item[0]),
            reverse=True,
        )
    ]
    sorted_merchants = [
        {
            "merchant": name,
            "amount": bucket["amount"],
            "count": bucket["count"],
        }
        for name, bucket in sorted(
            merchant_totals.items(),
            key=lambda item: (item[1]["amount"], item[1]["count"], item[0]),
            reverse=True,
        )
    ]
    top_day = None
    if daily_totals:
        top_day = max(daily_totals, key=lambda item: (item["amount"], item["count"], item["date"]))

    return {
        "label": label,
        "start_date": start_date,
        "end_date": end_date,
        "total_amount": total_amount,
        "transaction_count": transaction_count,
        "days_with_spend": len(daily_totals),
        "stored_day_count": len(snapshots),
        "category_totals": sorted_categories,
        "merchant_totals": sorted_merchants,
        "top_day": top_day,
    }


def resolve_feishu_report_targets():
    configured_receive_id = settings.CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID.strip()
    configured_receive_type = settings.CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID_TYPE.strip() or "chat_id"
    if configured_receive_id:
        return [(configured_receive_id, configured_receive_type)]

    if settings.CREDIT_CARD_REPORT_FEISHU_USE_LATEST_SESSION:
        session = FeishuChatSession.objects.exclude(chat_id="").order_by("-updated_at", "-id").first()
        if session:
            return [(session.chat_id, "chat_id")]

    raise CreditCardPushError(
        "No Feishu report recipient configured. Set CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID "
        "or chat with the Feishu bot first so the latest session can be reused."
    )


def push_text_report(text: str):
    targets = resolve_feishu_report_targets()
    for receive_id, receive_id_type in targets:
        send_text(receive_id, text, receive_id_type=receive_id_type)
    return len(targets)


def render_daily_push_text(snapshot: CreditCardDailySnapshot):
    lines = [
        f"信用卡日报 {snapshot.report_date.isoformat()}",
        f"总消费: {format_amount(snapshot.total_amount)} 元",
        f"交易笔数: {snapshot.matched_transaction_count}",
        f"检查邮件: {snapshot.checked_message_count}",
    ]

    if snapshot.matched_transaction_count == 0:
        lines.append("今天未识别到信用卡消费记录。")
        return "\n".join(lines)

    lines.append("")
    lines.append("类型分析:")
    for item in snapshot.category_totals[:5]:
        lines.append(
            f"- {item['category']}: {format_amount(Decimal(item['amount']))} 元 ({item['count']} 笔)"
        )

    lines.append("")
    lines.append("消费明细:")
    for index, item in enumerate(snapshot.transactions.all()[:8], start=1):
        lines.append(
            f"{index}. {item.occurred_at.strftime('%H:%M:%S')} | {item.category} | "
            f"{item.merchant} | {format_amount(item.amount)} 元"
        )

    return "\n".join(lines)


def render_period_push_text(summary):
    period_label = {
        "weekly": "信用卡周报",
        "monthly": "信用卡月报",
    }.get(summary["label"], "信用卡汇总")
    start_date = summary["start_date"].isoformat()
    end_date = summary["end_date"].isoformat()

    lines = [
        f"{period_label} {start_date} ~ {end_date}",
        f"总消费: {format_amount(summary['total_amount'])} 元",
        f"交易笔数: {summary['transaction_count']}",
        f"有消费天数: {summary['days_with_spend']}",
        f"已入库天数: {summary['stored_day_count']}",
    ]

    if summary["transaction_count"] == 0:
        lines.append("这个周期未识别到信用卡消费记录。")
        return "\n".join(lines)

    lines.append("")
    lines.append("类型分析:")
    for item in summary["category_totals"][:5]:
        lines.append(
            f"- {item['category']}: {format_amount(item['amount'])} 元 ({item['count']} 笔)"
        )

    if summary["top_day"]:
        lines.append("")
        lines.append(
            "最高消费日: "
            f"{summary['top_day']['date'].isoformat()} "
            f"{format_amount(summary['top_day']['amount'])} 元 "
            f"({summary['top_day']['count']} 笔)"
        )

    if summary["merchant_totals"]:
        lines.append("")
        lines.append("高频商户:")
        for item in summary["merchant_totals"][:5]:
            lines.append(
                f"- {item['merchant']}: {format_amount(item['amount'])} 元 ({item['count']} 笔)"
            )

    return "\n".join(lines)


def format_amount(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def current_report_date(run_date: date):
    return run_date - timedelta(days=settings.CREDIT_CARD_REPORT_DAILY_LAG_DAYS)


def should_push_weekly(run_date: date):
    return run_date.weekday() == settings.CREDIT_CARD_REPORT_WEEKLY_PUSH_WEEKDAY


def should_push_monthly(run_date: date):
    return run_date.day == settings.CREDIT_CARD_REPORT_MONTHLY_PUSH_DAY


def weekly_period_end(run_date: date):
    return current_report_date(run_date)


def weekly_period_start(run_date: date):
    return weekly_period_end(run_date) - timedelta(days=6)


def monthly_period_end(run_date: date):
    return current_report_date(run_date)


def monthly_period_start(run_date: date):
    return monthly_period_end(run_date).replace(day=1)


def sync_and_push_scheduled_reports(
    run_date: date,
    *,
    push=True,
    include_weekly=None,
    include_monthly=None,
):
    results = []

    daily_date = current_report_date(run_date)
    daily_snapshot = sync_daily_snapshot(daily_date)
    daily_text = render_daily_push_text(daily_snapshot)
    daily_target_count = 0
    if push:
        try:
            daily_target_count = push_text_report(daily_text)
        except (CreditCardPushError, FeishuConfigError, FeishuRequestError) as exc:
            raise CreditCardPushError(str(exc)) from exc
    results.append(("daily", daily_date, daily_target_count, daily_text))

    do_weekly = should_push_weekly(run_date) if include_weekly is None else include_weekly
    if do_weekly:
        week_start = weekly_period_start(run_date)
        week_end = weekly_period_end(run_date)
        ensure_daily_snapshots(week_start, week_end)
        weekly_text = render_period_push_text(build_period_summary(week_start, week_end, "weekly"))
        weekly_target_count = 0
        if push:
            try:
                weekly_target_count = push_text_report(weekly_text)
            except (CreditCardPushError, FeishuConfigError, FeishuRequestError) as exc:
                raise CreditCardPushError(str(exc)) from exc
        results.append(("weekly", week_end, weekly_target_count, weekly_text))

    do_monthly = should_push_monthly(run_date) if include_monthly is None else include_monthly
    if do_monthly:
        month_start = monthly_period_start(run_date)
        month_end = monthly_period_end(run_date)
        ensure_daily_snapshots(month_start, month_end)
        monthly_text = render_period_push_text(build_period_summary(month_start, month_end, "monthly"))
        monthly_target_count = 0
        if push:
            try:
                monthly_target_count = push_text_report(monthly_text)
            except (CreditCardPushError, FeishuConfigError, FeishuRequestError) as exc:
                raise CreditCardPushError(str(exc)) from exc
        results.append(("monthly", month_end, monthly_target_count, monthly_text))

    return results
