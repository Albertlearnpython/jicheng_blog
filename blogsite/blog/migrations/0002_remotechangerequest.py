import blog.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RemoteChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "approval_token",
                    models.CharField(
                        db_index=True,
                        default=blog.models.generate_approval_token,
                        max_length=8,
                        unique=True,
                    ),
                ),
                ("source_message_id", models.CharField(blank=True, max_length=128)),
                ("chat_id", models.CharField(blank=True, max_length=128)),
                ("user_open_id", models.CharField(blank=True, max_length=128)),
                ("prompt", models.TextField()),
                ("plan", models.JSONField(blank=True, default=dict)),
                ("execution_log", models.TextField(blank=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("applied", "Applied"),
                            ("rejected", "Rejected"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
