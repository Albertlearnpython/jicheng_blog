import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-change-me",
)
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost"])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "blog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "blogsite.urls"
TEMPLATES = []
WSGI_APPLICATION = "blogsite.wsgi.application"
ASGI_APPLICATION = "blogsite.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_TZ = env_bool("DJANGO_USE_TZ", True)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FEISHU_BASE_URL = os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "").strip()
FEISHU_EVENT_REPLY_TIMEOUT = int(os.environ.get("FEISHU_EVENT_REPLY_TIMEOUT", "15"))
FEISHU_REQUIRE_GROUP_MENTION = env_bool("FEISHU_REQUIRE_GROUP_MENTION", True)

CODEX_SSH_HOST = os.environ.get("CODEX_SSH_HOST", "").strip()
CODEX_SSH_PORT = int(os.environ.get("CODEX_SSH_PORT", "22"))
CODEX_SSH_USER = os.environ.get("CODEX_SSH_USER", "").strip()
CODEX_SSH_PASSWORD = os.environ.get("CODEX_SSH_PASSWORD", "")
CODEX_SSH_IDENTITY_FILE = os.environ.get("CODEX_SSH_IDENTITY_FILE", "").strip()
CODEX_SSH_CONNECT_TIMEOUT = int(os.environ.get("CODEX_SSH_CONNECT_TIMEOUT", "20"))

CODEX_BIN = os.environ.get("CODEX_BIN", "codex").strip() or "codex"
CODEX_PROFILE = os.environ.get("CODEX_PROFILE", "").strip()
CODEX_WORKDIR = os.environ.get("CODEX_WORKDIR", "/root").strip() or "/root"
CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-5.4").strip() or "gpt-5.4"
CODEX_REASONING_EFFORT = os.environ.get("CODEX_REASONING_EFFORT", "xhigh").strip() or "xhigh"
CODEX_SANDBOX = os.environ.get("CODEX_SANDBOX", "read-only").strip() or "read-only"
CODEX_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TIMEOUT_SECONDS", "180"))
CODEX_MAX_OUTPUT_CHARS = int(os.environ.get("CODEX_MAX_OUTPUT_CHARS", "12000"))
CODEX_DISABLE_RESPONSE_STORAGE = env_bool("CODEX_DISABLE_RESPONSE_STORAGE", True)

QQ_EMAIL_ADDRESS = os.environ.get("QQ_EMAIL_ADDRESS", "").strip()
QQ_EMAIL_APP_PASSWORD = os.environ.get("QQ_EMAIL_APP_PASSWORD", "").strip()
QQ_IMAP_HOST = os.environ.get("QQ_IMAP_HOST", "imap.qq.com").strip() or "imap.qq.com"
QQ_IMAP_PORT = int(os.environ.get("QQ_IMAP_PORT", "993"))

CREDIT_CARD_REPORT_TIME_ZONE = (
    os.environ.get("CREDIT_CARD_REPORT_TIME_ZONE", TIME_ZONE).strip() or TIME_ZONE
)
CREDIT_CARD_REPORT_MAILBOX = (
    os.environ.get("CREDIT_CARD_REPORT_MAILBOX", "INBOX").strip() or "INBOX"
)
CREDIT_CARD_REPORT_MAX_MESSAGES = int(os.environ.get("CREDIT_CARD_REPORT_MAX_MESSAGES", "200"))
CREDIT_CARD_REPORT_OUTPUT_DIR = (
    os.environ.get(
        "CREDIT_CARD_REPORT_OUTPUT_DIR",
        str(BASE_DIR.parent / "data" / "credit_card_reports"),
    ).strip()
    or str(BASE_DIR.parent / "data" / "credit_card_reports")
)
CREDIT_CARD_REPORT_DAILY_LAG_DAYS = int(os.environ.get("CREDIT_CARD_REPORT_DAILY_LAG_DAYS", "1"))
CREDIT_CARD_REPORT_WEEKLY_PUSH_WEEKDAY = int(
    os.environ.get("CREDIT_CARD_REPORT_WEEKLY_PUSH_WEEKDAY", "0")
)
CREDIT_CARD_REPORT_MONTHLY_PUSH_DAY = int(
    os.environ.get("CREDIT_CARD_REPORT_MONTHLY_PUSH_DAY", "1")
)
CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID = os.environ.get(
    "CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID",
    "",
).strip()
CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID_TYPE = (
    os.environ.get("CREDIT_CARD_REPORT_FEISHU_RECEIVE_ID_TYPE", "chat_id").strip()
    or "chat_id"
)
CREDIT_CARD_REPORT_FEISHU_USE_LATEST_SESSION = env_bool(
    "CREDIT_CARD_REPORT_FEISHU_USE_LATEST_SESSION",
    True,
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
}
