from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0002_remotechangerequest"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeishuChatSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chat_id", models.CharField(db_index=True, max_length=128, unique=True)),
                ("user_open_id", models.CharField(blank=True, max_length=128)),
                ("history", models.JSONField(blank=True, default=list)),
                ("memory", models.JSONField(blank=True, default=dict)),
                ("last_mode", models.CharField(blank=True, max_length=32)),
                ("last_pending_token", models.CharField(blank=True, max_length=8)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
    ]
