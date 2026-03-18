from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0004_terminalaccesslink"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Post",
        ),
        migrations.DeleteModel(
            name="RemoteChangeRequest",
        ),
        migrations.DeleteModel(
            name="TerminalAccessLink",
        ),
        migrations.RemoveField(
            model_name="feishuchatsession",
            name="history",
        ),
        migrations.RemoveField(
            model_name="feishuchatsession",
            name="memory",
        ),
        migrations.RemoveField(
            model_name="feishuchatsession",
            name="last_mode",
        ),
        migrations.RemoveField(
            model_name="feishuchatsession",
            name="last_pending_token",
        ),
        migrations.AddField(
            model_name="feishuchatsession",
            name="codex_thread_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="feishuchatsession",
            name="last_assistant_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="feishuchatsession",
            name="last_user_message",
            field=models.TextField(blank=True),
        ),
    ]
