import base64
import secrets

from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from .models import TerminalAccessLink


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


def create_terminal_access_code(chat_id, profile="shell"):
    normalized_profile = (profile or "shell").strip().lower() or "shell"
    for _ in range(8):
        code = secrets.token_hex(6)
        if not TerminalAccessLink.objects.filter(code=code).exists():
            TerminalAccessLink.objects.create(
                code=code,
                chat_id=(chat_id or "").strip(),
                profile=normalized_profile,
            )
            return code
    raise RuntimeError("Unable to allocate terminal access code.")


def resolve_terminal_access_code(code, max_age=None):
    resolved_max_age = (
        int(max_age)
        if max_age is not None
        else int(getattr(settings, "TERMINAL_WEB_TOKEN_MAX_AGE", 43200))
    )
    candidate = (code or "").strip().lower()
    if not candidate:
        raise signing.BadSignature("Missing terminal access code.")
    if len(candidate) < 6:
        raise signing.BadSignature("Terminal access code is too short.")

    link = TerminalAccessLink.objects.filter(code=candidate).first()
    if not link:
        matches = list(TerminalAccessLink.objects.filter(code__startswith=candidate).order_by("-created_at")[:2])
        if len(matches) != 1:
            raise signing.BadSignature("Invalid terminal access code.")
        link = matches[0]

    age_seconds = (timezone.now() - link.created_at).total_seconds()
    if age_seconds > resolved_max_age:
        raise signing.BadSignature("Terminal access code expired.")

    link.save(update_fields=["last_used_at"])
    return {
        "chat_id": link.chat_id,
        "profile": link.profile,
    }


def build_terminal_short_path(code):
    return reverse("terminal-short-page", kwargs={"code": code})
