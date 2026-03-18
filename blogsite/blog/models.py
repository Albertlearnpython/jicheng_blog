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
