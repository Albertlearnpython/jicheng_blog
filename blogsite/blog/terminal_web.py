from django.conf import settings
from django.core import signing


TERMINAL_WEB_SALT = "blog.terminal.web"


def create_terminal_access_token(chat_id, profile="shell"):
    payload = {
        "chat_id": (chat_id or "").strip(),
        "profile": (profile or "shell").strip().lower(),
    }
    return signing.dumps(payload, salt=TERMINAL_WEB_SALT)


def parse_terminal_access_token(token, max_age=None):
    resolved_max_age = (
        int(max_age)
        if max_age is not None
        else int(getattr(settings, "TERMINAL_WEB_TOKEN_MAX_AGE", 43200))
    )
    return signing.loads(token, salt=TERMINAL_WEB_SALT, max_age=resolved_max_age)


def build_terminal_ws_path(token):
    return f"/blog/ws/terminal/{token}/"
