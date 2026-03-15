import base64

from django.conf import settings
from django.core import signing


TERMINAL_WEB_SALT = "blog.terminal.web"


def _encode_public_token(raw_token):
    return base64.urlsafe_b64encode(raw_token.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_public_token(public_token):
    candidate = (public_token or "").strip()
    if not candidate:
        raise signing.BadSignature("Missing terminal token.")
    padding = "=" * (-len(candidate) % 4)
    decoded = base64.urlsafe_b64decode((candidate + padding).encode("ascii"))
    return decoded.decode("utf-8")


def create_terminal_access_token(chat_id, profile="shell"):
    payload = {
        "chat_id": (chat_id or "").strip(),
        "profile": (profile or "shell").strip().lower(),
    }
    raw_token = signing.dumps(payload, salt=TERMINAL_WEB_SALT)
    return _encode_public_token(raw_token)


def parse_terminal_access_token(token, max_age=None):
    resolved_max_age = (
        int(max_age)
        if max_age is not None
        else int(getattr(settings, "TERMINAL_WEB_TOKEN_MAX_AGE", 43200))
    )
    candidate = (token or "").strip()
    errors = []

    for raw_candidate in (candidate,):
        try:
            return signing.loads(raw_candidate, salt=TERMINAL_WEB_SALT, max_age=resolved_max_age)
        except signing.BadSignature as exc:
            errors.append(exc)

    try:
        decoded_token = _decode_public_token(candidate)
    except (ValueError, UnicodeDecodeError, signing.BadSignature) as exc:
        errors.append(exc)
    else:
        try:
            return signing.loads(decoded_token, salt=TERMINAL_WEB_SALT, max_age=resolved_max_age)
        except signing.BadSignature as exc:
            errors.append(exc)

    raise signing.BadSignature("Invalid terminal token.") from (errors[-1] if errors else None)


def build_terminal_ws_path(token):
    return f"/blog/ws/terminal/{token}/"
