from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0005_simplify_for_feishu_codex_bot"),
    ]

    operations = [
        migrations.CreateModel(
            name="CreditCardDailySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_date", models.DateField(db_index=True, unique=True)),
                ("checked_message_count", models.PositiveIntegerField(default=0)),
                ("matched_transaction_count", models.PositiveIntegerField(default=0)),
                ("total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("category_totals", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField()),
                ("json_report_path", models.CharField(blank=True, max_length=255)),
                ("text_report_path", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-report_date"],
            },
        ),
        migrations.CreateModel(
            name="CreditCardTransactionRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("merchant", models.CharField(max_length=255)),
                ("category", models.CharField(max_length=64)),
                ("subject", models.CharField(max_length=255)),
                ("sender", models.CharField(max_length=255)),
                ("snippet", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "daily_snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transactions",
                        to="blog.creditcarddailysnapshot",
                    ),
                ),
            ],
            options={
                "ordering": ["occurred_at", "id"],
            },
        ),
    ]
