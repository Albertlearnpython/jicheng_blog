from __future__ import annotations

import email
import imaplib
import json
import re
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings


SPEND_KEYWORDS = (
    "交易提醒",
    "动账提醒",
    "交易金额",
    "消费金额",
    "消费提醒",
    "消费",
    "刷卡",
    "支付",
    "快捷支付",
    "已入账",
    "本次交易",
    "本次消费",
    "商户",
    "尾号",
    "卡尾号",
    "信用卡",
)

IGNORE_KEYWORDS = (
    "账单",
    "还款",
    "分期",
    "额度",
    "调额",
    "激活",
    "办卡",
    "审批",
    "验证码",
    "营销",
    "推广",
    "优惠活动",
)

AMOUNT_CONTEXT_KEYWORDS = (
    "交易金额",
    "消费金额",
    "支付金额",
    "入账金额",
    "人民币",
    "本次交易",
    "本次消费",
    "消费",
    "支付",
)

CATEGORY_RULES = (
    (
        "餐饮",
        (
            "美团",
            "饿了么",
            "拉扎斯",
            "厨房",
            "咖啡",
            "奶茶",
            "茶饮",
            "餐厅",
            "饭店",
            "麦当劳",
            "肯德基",
            "瑞幸",
            "星巴克",
        ),
    ),
    ("出行", ("滴滴", "地铁", "公交", "高铁", "火车", "航空", "机票", "打车", "12306", "携程", "曹操出行")),
    ("购物", ("淘宝", "天猫", "京东", "拼多多", "超市", "商场", "便利店", "山姆", "永辉", "朴朴", "盒马")),
    ("生活服务", ("电费", "水费", "燃气", "物业", "停车", "话费", "宽带", "加油", "充电", "维修")),
    ("娱乐", ("电影", "影院", "游戏", "steam", "腾讯视频", "爱奇艺", "网易", "哔哩哔哩", "ktv")),
    ("数字服务", ("apple", "microsoft", "openai", "chatgpt", "claude", "cursor", "github", "腾讯云", "阿里云", "cloudflare")),
    ("教育", ("课程", "学费", "培训", "书店", "知识星球", "得到", "极客时间")),
    ("医疗健康", ("医院", "诊所", "药店", "药房", "体检", "问诊")),
    ("金融支出", ("保险", "利息", "手续费", "年费")),
)

MERCHANT_PATTERNS = (
    re.compile(r"(?:商户|商家|交易商户|消费商户|交易地点|消费地点|交易摘要|消费摘要)[:：]\s*([^\n\r]{2,80})"),
    re.compile(r"(?:在|于)([^，。,；;\n\r]{2,40})(?:消费|支付|交易)"),
)

AMOUNT_PATTERNS = (
    re.compile(
        r"(?:交易金额|消费金额|支付金额|入账金额|本次交易|本次消费|交易人民币|消费人民币)"
        r"[\s:：]*"
        r"(?:人民币|RMB|CNY|￥|¥)?\s*"
        r"([0-9][0-9,]*\.?[0-9]{0,2})",
        re.IGNORECASE,
    ),
    re.compile(r"(?:人民币|RMB|CNY|￥|¥)\s*([0-9][0-9,]*\.?[0-9]{0,2})", re.IGNORECASE),
    re.compile(r"([0-9][0-9,]*\.?[0-9]{1,2})\s*元"),
)

HTML_BREAKS_PATTERN = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>")
HTML_TAG_PATTERN = re.compile(r"(?s)<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"[ \t\xa0]+")
DIGEST_DATE_PATTERN = re.compile(r"(\d{4}/\d{2}/\d{2})\s*您的消费明细如下[:：]?")
TIME_LINE_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}$")
DETAIL_AMOUNT_PATTERN = re.compile(r"(?:CNY|人民币)\s*([0-9][0-9,]*\.?[0-9]{0,2})", re.IGNORECASE)


class CreditCardReportError(Exception):
    pass


@dataclass
class CreditCardTransaction:
    subject: str
    sender: str
    received_at: str
    amount: str
    merchant: str
    category: str
    snippet: str


@dataclass
class CreditCardDailyReport:
    report_date: str
    generated_at: str
    checked_message_count: int
    matched_message_count: int
    total_amount: str
    category_totals: list[dict]
    transactions: list[dict]


def generate_daily_report(target_date: date | None = None):
    tz = ZoneInfo(settings.CREDIT_CARD_REPORT_TIME_ZONE or settings.TIME_ZONE)
    report_date = target_date or datetime.now(tz).date()

    if not settings.QQ_EMAIL_ADDRESS:
        raise CreditCardReportError("QQ_EMAIL_ADDRESS is not configured.")
    if not settings.QQ_EMAIL_APP_PASSWORD:
        raise CreditCardReportError("QQ_EMAIL_APP_PASSWORD is not configured.")

    client = QQMailCreditCardClient(
        host=settings.QQ_IMAP_HOST,
        port=settings.QQ_IMAP_PORT,
        email_address=settings.QQ_EMAIL_ADDRESS,
        app_password=settings.QQ_EMAIL_APP_PASSWORD,
        mailbox=settings.CREDIT_CARD_REPORT_MAILBOX,
        max_messages=settings.CREDIT_CARD_REPORT_MAX_MESSAGES,
    )
    checked_count, transactions = client.fetch_transactions(report_date, tz)
    report = build_report(report_date, tz, checked_count, transactions)
    json_path, text_path = save_report(report, Path(settings.CREDIT_CARD_REPORT_OUTPUT_DIR))
    return report, json_path, text_path


class QQMailCreditCardClient:
    def __init__(self, host, port, email_address, app_password, mailbox="INBOX", max_messages=200):
        self.host = host
        self.port = int(port)
        self.email_address = email_address
        self.app_password = app_password
        self.mailbox = mailbox
        self.max_messages = max(int(max_messages), 1)

    def fetch_transactions(self, target_date: date, tz: ZoneInfo):
        checked_count = 0
        transactions = []

        with imaplib.IMAP4_SSL(self.host, self.port) as client:
            try:
                client.login(self.email_address, self.app_password)
            except imaplib.IMAP4.error as exc:
                raise CreditCardReportError(f"QQ IMAP login failed: {exc}") from exc

            status, _ = client.select(self.mailbox, readonly=True)
            if status != "OK":
                raise CreditCardReportError(f"Unable to open mailbox: {self.mailbox}")

            search_date = target_date.strftime("%d-%b-%Y")
            status, data = client.search(None, "SINCE", search_date)
            if status != "OK":
                raise CreditCardReportError("IMAP search failed.")

            message_ids = (data[0] or b"").split()
            message_ids = message_ids[-self.max_messages :]

            for message_id in message_ids:
                status, fetched = client.fetch(message_id, "(RFC822)")
                if status != "OK":
                    continue
                raw_message = _extract_rfc822_bytes(fetched)
                if not raw_message:
                    continue
                checked_count += 1
                transactions.extend(parse_credit_card_transactions(raw_message, target_date, tz))

            client.close()
            client.logout()

        return checked_count, dedupe_transactions(transactions)


def _extract_rfc822_bytes(fetched_data):
    for item in fetched_data or []:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        if isinstance(item[1], bytes):
            return item[1]
    return b""


def parse_credit_card_transactions(raw_message: bytes, target_date: date, tz: ZoneInfo):
    message = email.message_from_bytes(raw_message)
    subject = decode_mime_text(message.get("Subject", ""))
    sender = decode_mime_text(message.get("From", ""))
    body = extract_message_text(message)
    digest_transactions = parse_daily_digest_transactions(subject, sender, body, target_date, tz)
    if digest_transactions:
        return digest_transactions

    received_at = extract_received_at(message, tz)
    if received_at.date() != target_date:
        return []

    if not looks_like_credit_card_spend(subject, sender, body):
        return []

    amount = extract_amount(body)
    if amount is None:
        return []

    merchant = extract_merchant(body, subject)
    category = categorize_transaction(subject, merchant, body)
    snippet = build_snippet(body)
    return [
        CreditCardTransaction(
            subject=subject or "(无主题)",
            sender=sender or "(未知发件人)",
            received_at=received_at.isoformat(),
            amount=format_money(amount),
            merchant=merchant,
            category=category,
            snippet=snippet,
        )
    ]


def parse_credit_card_transaction(raw_message: bytes, target_date: date, tz: ZoneInfo):
    transactions = parse_credit_card_transactions(raw_message, target_date, tz)
    return transactions[0] if transactions else None


def parse_daily_digest_transactions(subject, sender, body, target_date: date, tz: ZoneInfo):
    if "每日信用管家" not in (subject or ""):
        return []
    if "招商银行信用卡" not in (sender or "") and "message.cmbchina.com" not in (sender or "").lower():
        return []

    lines = [line.strip() for line in (body or "").splitlines() if line.strip()]
    digest_date = None
    digest_start_index = None
    for index, line in enumerate(lines):
        match = DIGEST_DATE_PATTERN.search(line)
        if not match:
            continue
        digest_date = datetime.strptime(match.group(1), "%Y/%m/%d").date()
        digest_start_index = index + 1
        break

    if digest_date != target_date or digest_start_index is None:
        return []

    transactions = []
    cursor = digest_start_index
    while cursor + 2 < len(lines):
        time_line = lines[cursor]
        amount_line = lines[cursor + 1]
        detail_line = lines[cursor + 2]

        if not TIME_LINE_PATTERN.match(time_line):
            cursor += 1
            continue

        amount_match = DETAIL_AMOUNT_PATTERN.search(amount_line)
        if not amount_match:
            cursor += 1
            continue

        amount = parse_money(amount_match.group(1))
        if amount is None:
            cursor += 1
            continue

        received_at = datetime.combine(
            digest_date,
            datetime.strptime(time_line, "%H:%M:%S").time(),
            tzinfo=tz,
        )
        merchant = extract_merchant(detail_line, subject)
        category = categorize_transaction(subject, merchant, detail_line)
        transactions.append(
            CreditCardTransaction(
                subject=subject or "(无主题)",
                sender=sender or "(未知发件人)",
                received_at=received_at.isoformat(),
                amount=format_money(amount),
                merchant=merchant,
                category=category,
                snippet=detail_line[:160],
            )
        )
        cursor += 3

    return transactions


def decode_mime_text(value):
    if not value:
        return ""

    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            encoding = charset or "utf-8"
            try:
                parts.append(chunk.decode(encoding, errors="replace"))
            except LookupError:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def extract_received_at(message: Message, tz: ZoneInfo):
    raw_date = decode_mime_text(message.get("Date", ""))
    if raw_date:
        try:
            parsed = parsedate_to_datetime(raw_date)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    return datetime.now(tz)


def extract_message_text(message: Message):
    plain_parts = []
    html_parts = []

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue

        content_type = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")

        if content_type == "text/plain":
            plain_parts.append(text)
        elif content_type == "text/html":
            html_parts.append(html_to_text(text))

    combined = "\n".join(part for part in plain_parts + html_parts if part.strip())
    return normalize_text(combined)


def html_to_text(html):
    text = HTML_BREAKS_PATTERN.sub("\n", html or "")
    text = HTML_TAG_PATTERN.sub(" ", text)
    return unescape(text)


def normalize_text(text):
    lines = []
    for raw_line in (text or "").splitlines():
        cleaned = WHITESPACE_PATTERN.sub(" ", raw_line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def looks_like_credit_card_spend(subject, sender, body):
    content = "\n".join([subject or "", sender or "", body or ""]).lower()
    positive_hits = sum(1 for keyword in SPEND_KEYWORDS if keyword.lower() in content)
    negative_hits = sum(1 for keyword in IGNORE_KEYWORDS if keyword.lower() in content)
    has_card_hint = any(
        hint in content
        for hint in ("信用卡", "银行卡", "卡尾号", "尾号", "银联", "bank", "银行", "@", "card")
    )
    return negative_hits == 0 and positive_hits >= 2 and has_card_hint


def extract_amount(text):
    lines = [line for line in (text or "").splitlines() if line]
    for line in lines:
        if not any(keyword in line for keyword in AMOUNT_CONTEXT_KEYWORDS):
            continue
        amount = _extract_first_amount(line)
        if amount is not None:
            return amount

    combined = "\n".join(lines)
    for keyword in AMOUNT_CONTEXT_KEYWORDS:
        index = combined.find(keyword)
        if index < 0:
            continue
        window = combined[max(index - 24, 0) : index + 80]
        amount = _extract_first_amount(window)
        if amount is not None:
            return amount

    return None


def _extract_first_amount(text):
    for pattern in AMOUNT_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        amount = parse_money(match.group(1))
        if amount is not None and amount > Decimal("0"):
            return amount
    return None


def parse_money(value):
    cleaned = (value or "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def extract_merchant(body, subject):
    content = "\n".join([body or "", subject or ""])
    for pattern in MERCHANT_PATTERNS:
        match = pattern.search(content)
        if not match:
            continue
        merchant = match.group(1).strip(" ：:-")
        merchant = merchant[:80]
        if merchant:
            return merchant

    body_text = body or ""
    if "消费" in body_text:
        suffix = body_text.split("消费", 1)[1].strip(" ：:-")
        if suffix:
            return suffix[:80]

    for line in (body or "").splitlines():
        if any(keyword in line for keyword in ("微信支付", "支付宝", "财付通", "美团", "滴滴", "淘宝", "京东")):
            return line[:80]

    return "未识别商户"


def categorize_transaction(subject, merchant, body):
    content = "\n".join([subject or "", merchant or "", body or ""]).lower()
    for category, keywords in CATEGORY_RULES:
        if any(keyword.lower() in content for keyword in keywords):
            return category
    return "其他"


def build_snippet(body):
    text = (body or "").replace("\n", " ").strip()
    if len(text) <= 160:
        return text
    return text[:157].rstrip() + "..."


def build_report(target_date: date, tz: ZoneInfo, checked_count, transactions):
    total_amount = Decimal("0.00")
    category_totals = defaultdict(lambda: {"amount": Decimal("0.00"), "count": 0})

    for transaction in transactions:
        amount = Decimal(transaction.amount)
        total_amount += amount
        bucket = category_totals[transaction.category]
        bucket["amount"] += amount
        bucket["count"] += 1

    serialized_categories = []
    for category, stats in sorted(
        category_totals.items(),
        key=lambda item: (item[1]["amount"], item[1]["count"], item[0]),
        reverse=True,
    ):
        serialized_categories.append(
            {
                "category": category,
                "amount": format_money(stats["amount"]),
                "count": stats["count"],
            }
        )

    return CreditCardDailyReport(
        report_date=target_date.isoformat(),
        generated_at=datetime.now(tz).isoformat(),
        checked_message_count=checked_count,
        matched_message_count=len(transactions),
        total_amount=format_money(total_amount),
        category_totals=serialized_categories,
        transactions=[asdict(transaction) for transaction in transactions],
    )


def save_report(report: CreditCardDailyReport, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.report_date}.json"
    text_path = output_dir / f"{report.report_date}.txt"

    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text_path.write_text(render_text_report(report), encoding="utf-8")
    return json_path, text_path


def render_text_report(report: CreditCardDailyReport):
    lines = [
        f"信用卡消费日报 - {report.report_date}",
        f"生成时间: {report.generated_at}",
        f"检查邮件数: {report.checked_message_count}",
        f"识别消费数: {report.matched_message_count}",
        f"总消费: {report.total_amount} 元",
        "",
        "类型汇总:",
    ]

    if report.category_totals:
        for item in report.category_totals:
            lines.append(f"- {item['category']}: {item['amount']} 元 ({item['count']} 笔)")
    else:
        lines.append("- 今天未识别到信用卡消费邮件")

    lines.append("")
    lines.append("消费明细:")

    if report.transactions:
        for index, item in enumerate(report.transactions, start=1):
            received = _format_iso_time(item["received_at"])
            lines.append(
                f"{index}. {received} | {item['category']} | {item['merchant']} | {item['amount']} 元"
            )
            lines.append(f"   标题: {item['subject']}")
            lines.append(f"   发件人: {item['sender']}")
            if item["snippet"]:
                lines.append(f"   摘要: {item['snippet']}")
    else:
        lines.append("1. 今天未识别到信用卡消费邮件")

    lines.append("")
    return "\n".join(lines)


def _format_iso_time(value):
    try:
        return datetime.fromisoformat(value).strftime("%H:%M:%S")
    except ValueError:
        return value


def format_money(amount):
    return str(Decimal(amount).quantize(Decimal("0.01")))


def dedupe_transactions(transactions):
    unique = {}
    for transaction in transactions:
        key = (
            transaction.received_at,
            transaction.amount,
            transaction.merchant,
        )
        unique[key] = transaction
    return sorted(unique.values(), key=lambda item: item.received_at)
