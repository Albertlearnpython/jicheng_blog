import secrets

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def generate_approval_token():
    return secrets.token_hex(4)

class Post(models.Model):
    title = models.CharField(max_length=100)
    content = models.TextField()
    date_posted = models.DateTimeField(default=timezone.now)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    
    def __str__(self):
        return self.title


class RemoteChangeRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_APPLIED = "applied"
    STATUS_REJECTED = "rejected"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_APPLIED, "Applied"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_FAILED, "Failed"),
    ]

    approval_token = models.CharField(
        max_length=8,
        unique=True,
        db_index=True,
        default=generate_approval_token,
    )
    source_message_id = models.CharField(max_length=128, blank=True)
    chat_id = models.CharField(max_length=128, blank=True)
    user_open_id = models.CharField(max_length=128, blank=True)
    prompt = models.TextField()
    plan = models.JSONField(default=dict, blank=True)
    execution_log = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.approval_token} {self.status}"
