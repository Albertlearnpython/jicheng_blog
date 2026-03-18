import secrets

from django.db import models


def generate_approval_token():
    return secrets.token_hex(4)


class FeishuChatSession(models.Model):
    chat_id = models.CharField(max_length=128, unique=True, db_index=True)
    user_open_id = models.CharField(max_length=128, blank=True)
    codex_thread_id = models.CharField(max_length=64, blank=True)
    last_user_message = models.TextField(blank=True)
    last_assistant_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.chat_id


class CreditCardDailySnapshot(models.Model):
    report_date = models.DateField(unique=True, db_index=True)
    checked_message_count = models.PositiveIntegerField(default=0)
    matched_transaction_count = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category_totals = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField()
    json_report_path = models.CharField(max_length=255, blank=True)
    text_report_path = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-report_date"]

    def __str__(self):
        return self.report_date.isoformat()


class CreditCardTransactionRecord(models.Model):
    daily_snapshot = models.ForeignKey(
        CreditCardDailySnapshot,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    occurred_at = models.DateTimeField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    merchant = models.CharField(max_length=255)
    category = models.CharField(max_length=64)
    subject = models.CharField(max_length=255)
    sender = models.CharField(max_length=255)
    snippet = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at", "id"]

    def __str__(self):
        return f"{self.daily_snapshot.report_date} {self.amount} {self.merchant}"
