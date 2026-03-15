from django.core import signing

from .models import FeishuChatSession
from .terminal_web import parse_terminal_access_token


class TerminalSessionError(Exception):
    pass


def get_terminal_state(session):
    memory = dict(session.memory or {})
    state = memory.get("terminal") or {}
    if not isinstance(state, dict):
        state = {}
    return dict(state)


def update_terminal_state(session, **updates):
    state = get_terminal_state(session)
    state.update(updates)
    memory = dict(session.memory or {})
    memory["terminal"] = state
    session.memory = memory
    session.save(update_fields=["memory", "updated_at"])
    return dict(state)


def clear_terminal_state(session):
    return update_terminal_state(
        session,
        active=False,
        profile="",
        passthrough=False,
        cwd="",
        program="",
        output="",
    )


def resolve_terminal_session(token):
    try:
        payload = parse_terminal_access_token(token)
    except signing.BadSignature as exc:
        raise TerminalSessionError("Invalid terminal token.") from exc

    chat_id = (payload.get("chat_id") or "").strip()
    if not chat_id:
        raise TerminalSessionError("Missing terminal chat id.")

    session = FeishuChatSession.objects.filter(chat_id=chat_id).first()
    if not session:
        raise TerminalSessionError("Terminal session was not found.")

    return session, payload


def terminal_output_delta(previous_output, current_output):
    previous_lines = (previous_output or "").splitlines()
    current_lines = (current_output or "").splitlines()
    max_overlap = min(len(previous_lines), len(current_lines))

    overlap = 0
    for size in range(max_overlap, 0, -1):
        if previous_lines[-size:] == current_lines[:size]:
            overlap = size
            break

    delta_lines = current_lines[overlap:]
    if not delta_lines:
        delta_lines = current_lines[-20:]
    return "\n".join(delta_lines).strip()


def terminal_snapshot_payload(state, snapshot, fallback_profile="shell", replace=False, event="snapshot"):
    terminal_state = dict(state or {})
    return {
        "ok": True,
        "event": event,
        "replace": bool(replace),
        "active": bool(snapshot.get("exists", True)),
        "profile": snapshot.get("profile") or terminal_state.get("profile") or fallback_profile or "shell",
        "cwd": snapshot.get("cwd", ""),
        "program": snapshot.get("program", ""),
        "output": snapshot.get("output", ""),
        "passthrough": bool(terminal_state.get("passthrough", True)),
    }
