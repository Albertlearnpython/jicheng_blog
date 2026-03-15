from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0003_feishuchatsession"),
    ]

    operations = [
        migrations.CreateModel(
            name="TerminalAccessLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=16, unique=True)),
                ("chat_id", models.CharField(db_index=True, max_length=128)),
                ("profile", models.CharField(default="shell", max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
