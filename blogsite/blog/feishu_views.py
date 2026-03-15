import hashlib
import json
import logging
import re
import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .feishu_calendar import (
    create_calendar_event,
    delete_calendar_event,
    list_calendar_events,
)
from .feishu_client import FeishuConfigError, FeishuError, FeishuRequestError, reply_text, send_text
from .models import FeishuChatSession, RemoteChangeRequest
from .openai_client import OpenAIConfigError, OpenAIRequestError
from .remote_agent import (
    RemotePlanningError,
    assess_plan_risk,
    answer_general_question,
    answer_read_only_request,
    apply_change_plan,
    build_change_request_plan,
    classify_user_request,
    format_plan_for_user,
)
from .remote_executor import RemoteExecutor, RemoteExecutorConfigError, RemoteExecutorError
from .remote_terminal import RemoteTerminalError, RemoteTerminalManager
from .terminal_web import create_terminal_access_token

logger = logging.getLogger(__name__)
SESSION_HISTORY_LIMIT = 12
_SESSION_UNSET = object()

APPROVE_PATTERN = re.compile(r"^/(approve|批准)\s+([0-9a-f]{8})$", re.IGNORECASE)
REJECT_PATTERN = re.compile(r"^/(reject|拒绝)\s+([0-9a-f]{8})$", re.IGNORECASE)
STATUS_PATTERN = re.compile(r"^/(status|状态)\s+([0-9a-f]{8})$", re.IGNORECASE)
CONFIRM_PATTERN = re.compile(r"^(确认执行|确认|继续执行|同意执行|批准执行)$", re.IGNORECASE)
DECLINE_PATTERN = re.compile(r"^(取消执行|拒绝执行)$", re.IGNORECASE)

CALENDAR_HELP_TEXT = """日程命令:
/calendar create 2026-03-15 14:00 15:00 需求评审 | 可选描述
/calendar list
/calendar list 2026-03-15
/calendar delete <event_id>

说明:
- 当前版本默认操作机器人的日历
- 创建后会自动把你加入参与人
- `list` 默认查看今天"""

HELP_TEXT = """linuxclaw bot commands:
/help
/chat <question>
/repo <request>
/calendar create 2026-03-15 14:00 15:00 标题 | 可选描述
/calendar list [YYYY-MM-DD]
/calendar delete <event_id>
/approve <token>
/reject <token>
/status <token>

直接发送自然语言也可以，我会自动判断模式：
- 普通问答：直接回答
- 服务器只读查询：直接执行并返回结果
- 低风险仓库修改：我会先看项目，再直接执行
- 高风险仓库/服务器任务：我会先给计划，再等你审批

示例：
- /chat 你和 openclaw 有什么区别？
- /repo 帮我检查首页报错原因
- /calendar create 2026-03-15 14:00 15:00 产品评审 | 对齐需求
- 帮我修改 Django 路由并补一个测试"""

CALENDAR_HELP_PATTERN = re.compile(r"^/(calendar|schedule|日程)(?:\s+(help|帮助))?$", re.IGNORECASE)
CALENDAR_CREATE_PATTERN = re.compile(
    r"^/(calendar|schedule|日程)\s+(create|new|add|创建)\s+"
    r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\d{2}:\d{2})\s+(.+)$",
    re.IGNORECASE,
)
CALENDAR_LIST_PATTERN = re.compile(
    r"^/(calendar|schedule|日程)\s+(list|ls|today|查询|列表)(?:\s+(\d{4}-\d{2}-\d{2}))?$",
    re.IGNORECASE,
)
CALENDAR_DELETE_PATTERN = re.compile(
    r"^/(calendar|schedule|日程)\s+(delete|remove|cancel|删除|取消)\s+(\S+)$",
    re.IGNORECASE,
)
CALENDAR_PREFIX_PATTERN = re.compile(r"^/(calendar|schedule|日程)\b", re.IGNORECASE)
TERMINAL_PREFIX_PATTERN = re.compile(r"^/(term|terminal)\b", re.IGNORECASE)
TERMINAL_HELP_TEXT = """terminal commands:
/term help
/term open [shell|codex] [path]
/term status
/term read
/term send <command>
/term key <ctrl-c|enter|esc|tab|up|down|left|right|backspace|delete>
/term ctrl-c
/term mode <on|off>
/term close

Notes:
- `/term open codex` opens a persistent Codex CLI session on the Linux server.
- After opening, passthrough mode is enabled by default: plain text messages are sent to the terminal.
- Use `/chat ...` or `/term mode off` when you want normal bot Q&A again.
"""
HELP_TEXT += "\n\n" + TERMINAL_HELP_TEXT


def _normalize_session_history(history):
    normalized = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized[-SESSION_HISTORY_LIMIT:]


def _save_session(session, update_fields):
    if not session:
        return
    fields = []
    for field in list(update_fields) + ["updated_at"]:
        if field and field not in fields:
            fields.append(field)
    session.save(update_fields=fields)


def _get_chat_session(chat_id, user_open_id=""):
    if not chat_id:
        return None

    session, _ = FeishuChatSession.objects.get_or_create(
        chat_id=chat_id,
        defaults={"user_open_id": user_open_id},
    )

    update_fields = []
    normalized_history = _normalize_session_history(session.history)
    if session.history != normalized_history:
        session.history = normalized_history
        update_fields.append("history")
    if user_open_id and session.user_open_id != user_open_id:
        session.user_open_id = user_open_id
        update_fields.append("user_open_id")
    if update_fields:
        _save_session(session, update_fields)
    return session


def _set_session_state(session, *, last_mode=None, last_pending_token=_SESSION_UNSET, memory_updates=None):
    if not session:
        return

    update_fields = []
    if last_mode is not None and session.last_mode != last_mode:
        session.last_mode = last_mode
        update_fields.append("last_mode")
    if last_pending_token is not _SESSION_UNSET:
        normalized_token = (last_pending_token or "").strip()
        if session.last_pending_token != normalized_token:
            session.last_pending_token = normalized_token
            update_fields.append("last_pending_token")
    if memory_updates:
        memory = dict(session.memory or {})
        memory.update(memory_updates)
        if session.memory != memory:
            session.memory = memory
            update_fields.append("memory")
    if update_fields:
        _save_session(session, update_fields)


def _append_session_turn(
    session,
    role,
    content,
    *,
    last_mode=None,
    last_pending_token=_SESSION_UNSET,
    memory_updates=None,
):
    if not session:
        return

    update_fields = []
    text = (content or "").strip()
    history = _normalize_session_history(session.history)
    if text:
        history.append({"role": role, "content": text})
        history = history[-SESSION_HISTORY_LIMIT:]
        if session.history != history:
            session.history = history
            update_fields.append("history")

    if last_mode is not None and session.last_mode != last_mode:
        session.last_mode = last_mode
        update_fields.append("last_mode")
    if last_pending_token is not _SESSION_UNSET:
        normalized_token = (last_pending_token or "").strip()
        if session.last_pending_token != normalized_token:
            session.last_pending_token = normalized_token
            update_fields.append("last_pending_token")
    if memory_updates:
        memory = dict(session.memory or {})
        memory.update(memory_updates)
        if session.memory != memory:
            session.memory = memory
            update_fields.append("memory")

    if update_fields:
        _save_session(session, update_fields)


def _send_session_message(
    chat_id,
    text,
    *,
    session=None,
    last_mode=None,
    last_pending_token=_SESSION_UNSET,
    memory_updates=None,
):
    _send_chat_message(chat_id, text)
    _append_session_turn(
        session,
        "assistant",
        text,
        last_mode=last_mode,
        last_pending_token=last_pending_token,
        memory_updates=memory_updates,
    )


def _merge_notes(*groups):
    merged = []
    for group in groups:
        for item in group or []:
            text = (item or "").strip()
            if text and text not in merged:
                merged.append(text)
    return merged


def _build_terminal_web_url(chat_id, profile):
    base_url = (settings.APP_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        return ""
    token = create_terminal_access_token(chat_id, profile=profile)
    return f"{base_url}{reverse('terminal-page', kwargs={'token': token})}"


def _terminal_access_allowed(sender_open_id):
    allowed_open_ids = set(settings.FEISHU_TERMINAL_ALLOWED_OPEN_IDS)
    if not allowed_open_ids:
        return True
    return bool(sender_open_id and sender_open_id in allowed_open_ids)


def _terminal_state(session):
    if not session:
        return {}
    memory = session.memory or {}
    state = memory.get("terminal") or {}
    if not isinstance(state, dict):
        return {}
    return dict(state)


def _update_terminal_state(session, **updates):
    if not session:
        return
    state = _terminal_state(session)
    state.update(updates)
    _set_session_state(session, memory_updates={"terminal": state})


def _clear_terminal_state(session):
    _update_terminal_state(
        session,
        active=False,
        profile="",
        passthrough=False,
        cwd="",
        program="",
        output="",
    )


def _terminal_has_active_passthrough(session):
    state = _terminal_state(session)
    return bool(state.get("active") and state.get("passthrough"))


def _terminal_output_delta(previous_output, current_output):
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


def _format_terminal_snapshot(snapshot, session=None, title="终端输出", use_delta=False):
    state = _terminal_state(session)
    current_output = (snapshot.get("output") or "").strip()
    output_text = current_output
    if use_delta:
        output_text = _terminal_output_delta(state.get("output", ""), current_output)

    profile = snapshot.get("profile") or state.get("profile") or "shell"
    lines = [
        f"{title}: {profile}",
        f"目录: {snapshot.get('cwd') or '-'}",
        f"程序: {snapshot.get('program') or '-'}",
        f"透传: {'on' if state.get('passthrough') else 'off'}",
        "",
        output_text or "(no output)",
    ]
    return "\n".join(lines)


def _parse_terminal_command(text):
    value = (text or "").strip()
    if not TERMINAL_PREFIX_PATTERN.match(value):
        return None

    body = TERMINAL_PREFIX_PATTERN.sub("", value, count=1).strip()
    if not body or body.lower() == "help":
        return {"action": "help"}

    parts = body.split(None, 1)
    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action == "open":
        profile = "shell"
        cwd = ""
        if rest:
            open_parts = rest.split(None, 1)
            first = open_parts[0].lower()
            if first in {"shell", "codex"}:
                profile = first
                cwd = open_parts[1].strip() if len(open_parts) > 1 else ""
            else:
                cwd = rest
        return {"action": "open", "profile": profile, "cwd": cwd}

    if action in {"status", "read", "close"}:
        return {"action": action}

    if action in {"send", "run"}:
        return {"action": "send", "text": rest}

    if action == "mode":
        mode = rest.lower()
        if mode in {"on", "off"}:
            return {"action": "mode", "enabled": mode == "on"}
        return {"action": "invalid"}

    if action == "key":
        return {"action": "key", "key": rest.lower()}

    if action in {"ctrl-c", "interrupt"}:
        return {"action": "key", "key": "ctrl-c"}

    if action in {"enter", "esc", "tab", "up", "down", "left", "right", "backspace", "delete"}:
        return {"action": "key", "key": action}

    return {"action": "invalid"}


def _handle_terminal_command(chat_id, text, sender_open_id, session=None):
    command = _parse_terminal_command(text)
    if command is None:
        return False

    if command["action"] == "help":
        _send_session_message(chat_id, TERMINAL_HELP_TEXT, session=session, last_mode="terminal")
        return True

    if not settings.FEISHU_TERMINAL_ENABLED:
        _send_session_message(chat_id, "终端模式当前未启用。", session=session, last_mode="terminal")
        return True

    if not _terminal_access_allowed(sender_open_id):
        _send_session_message(chat_id, "你当前没有终端模式权限。", session=session, last_mode="terminal")
        return True

    manager = RemoteTerminalManager()

    try:
        if command["action"] == "open":
            snapshot = manager.open(chat_id, profile=command["profile"], cwd=command["cwd"])
            snapshot["profile"] = command["profile"]
            _update_terminal_state(
                session,
                active=True,
                profile=command["profile"],
                passthrough=True,
                cwd=snapshot.get("cwd", ""),
                program=snapshot.get("program", ""),
                output=snapshot.get("output", ""),
            )
            message = _format_terminal_snapshot(
                snapshot,
                session=session,
                title="终端已打开" if snapshot.get("created") else "终端已连接",
            )
            terminal_url = _build_terminal_web_url(chat_id, command["profile"])
            message += (
                "\n\n现在你可以直接发送命令到服务器。"
                "\n退出透传: /term mode off"
                "\n读取最新输出: /term read"
                "\n发送 Ctrl-C: /term ctrl-c"
                "\n关闭会话: /term close"
            )
            if terminal_url:
                message += f"\n网页终端: {terminal_url}"
            _send_session_message(chat_id, message, session=session, last_mode="terminal")
            return True

        if command["action"] == "mode":
            state = _terminal_state(session)
            if not state.get("active"):
                _send_session_message(chat_id, "当前没有活动终端会话。", session=session, last_mode="terminal")
                return True
            _update_terminal_state(session, passthrough=command["enabled"])
            _send_session_message(
                chat_id,
                f"终端透传已{'开启' if command['enabled'] else '关闭'}。",
                session=session,
                last_mode="terminal",
            )
            return True

        if command["action"] == "close":
            manager.close(chat_id)
            _clear_terminal_state(session)
            _send_session_message(chat_id, "终端会话已关闭。", session=session, last_mode="terminal")
            return True

        if command["action"] == "status":
            snapshot = manager.status(chat_id)
            if not snapshot.get("exists"):
                _clear_terminal_state(session)
                _send_session_message(chat_id, "当前没有活动终端会话。", session=session, last_mode="terminal")
                return True
            state = _terminal_state(session)
            snapshot["profile"] = state.get("profile") or "shell"
            _update_terminal_state(
                session,
                active=True,
                cwd=snapshot.get("cwd", ""),
                program=snapshot.get("program", ""),
                output=snapshot.get("output", ""),
            )
            _send_session_message(
                chat_id,
                _format_terminal_snapshot(snapshot, session=session, title="终端状态"),
                session=session,
                last_mode="terminal",
            )
            return True

        if command["action"] == "read":
            snapshot = manager.read(chat_id)
            state = _terminal_state(session)
            snapshot["profile"] = state.get("profile") or "shell"
            message = _format_terminal_snapshot(
                snapshot,
                session=session,
                title="终端输出",
                use_delta=True,
            )
            _update_terminal_state(
                session,
                active=True,
                cwd=snapshot.get("cwd", ""),
                program=snapshot.get("program", ""),
                output=snapshot.get("output", ""),
            )
            _send_session_message(chat_id, message, session=session, last_mode="terminal")
            return True

        if command["action"] == "send":
            snapshot = manager.send(chat_id, command["text"], enter=True)
            state = _terminal_state(session)
            snapshot["profile"] = state.get("profile") or "shell"
            message = _format_terminal_snapshot(
                snapshot,
                session=session,
                title="终端回显",
                use_delta=True,
            )
            _update_terminal_state(
                session,
                active=True,
                cwd=snapshot.get("cwd", ""),
                program=snapshot.get("program", ""),
                output=snapshot.get("output", ""),
            )
            _send_session_message(chat_id, message, session=session, last_mode="terminal")
            return True

        if command["action"] == "key":
            snapshot = manager.send_key(chat_id, command["key"])
            state = _terminal_state(session)
            snapshot["profile"] = state.get("profile") or "shell"
            message = _format_terminal_snapshot(
                snapshot,
                session=session,
                title=f"终端按键: {command['key']}",
                use_delta=True,
            )
            _update_terminal_state(
                session,
                active=True,
                cwd=snapshot.get("cwd", ""),
                program=snapshot.get("program", ""),
                output=snapshot.get("output", ""),
            )
            _send_session_message(chat_id, message, session=session, last_mode="terminal")
            return True

        _send_session_message(chat_id, TERMINAL_HELP_TEXT, session=session, last_mode="terminal")
        return True
    except (RemoteTerminalError, RemoteExecutorConfigError, RemoteExecutorError) as exc:
        _send_session_message(chat_id, f"终端操作失败: {exc}", session=session, last_mode="terminal")
        return True


def _handle_terminal_passthrough(chat_id, text, sender_open_id, session=None):
    if not settings.FEISHU_TERMINAL_ENABLED:
        return False
    if text.startswith("/"):
        return False
    if not _terminal_has_active_passthrough(session):
        return False
    if not _terminal_access_allowed(sender_open_id):
        _send_session_message(chat_id, "你当前没有终端模式权限。", session=session, last_mode="terminal")
        return True

    manager = RemoteTerminalManager()
    try:
        snapshot = manager.send(chat_id, text, enter=True)
    except (RemoteTerminalError, RemoteExecutorConfigError, RemoteExecutorError) as exc:
        _send_session_message(chat_id, f"终端操作失败: {exc}", session=session, last_mode="terminal")
        return True

    state = _terminal_state(session)
    snapshot["profile"] = state.get("profile") or "shell"
    message = _format_terminal_snapshot(
        snapshot,
        session=session,
        title="终端回显",
        use_delta=True,
    )
    _update_terminal_state(
        session,
        active=True,
        cwd=snapshot.get("cwd", ""),
        program=snapshot.get("program", ""),
        output=snapshot.get("output", ""),
    )
    _send_session_message(chat_id, message, session=session, last_mode="terminal")
    return True


def _is_valid_callback(payload):
    configured = settings.FEISHU_VERIFICATION_TOKEN
    if not configured:
        return True
    header = payload.get("header") or {}
    incoming = header.get("token") or payload.get("token")
    return incoming == configured


def _extract_text_message(event):
    message = event.get("message") or {}
    if message.get("message_type") != "text":
        return ""
    try:
        content = json.loads(message.get("content") or "{}")
    except json.JSONDecodeError:
        return ""
    text = (content.get("text") or "").strip()
    for mention in message.get("mentions", []):
        name = mention.get("name")
        if name:
            text = re.sub(rf"^@{re.escape(name)}\s*", "", text)
    return text.strip()


def _shorten(text, limit=1500):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _event_cache_key(event_id):
    digest = hashlib.sha256((event_id or "").encode("utf-8")).hexdigest()
    return f"feishu:event:{digest}"


def _send_chat_message(chat_id, text):
    if not chat_id:
        return
    send_text(chat_id, _shorten(text, limit=3500))


def _format_execution_result(result):
    lines = [f"执行完成: {result['summary']}"]
    if result.get("files"):
        lines.append("修改文件: " + ", ".join(result["files"]))
    if result.get("tests"):
        test_item = result["tests"][0]
        lines.append(f"测试: {test_item['command']}")
        if test_item.get("output"):
            lines.append(_shorten(test_item["output"], limit=1200))
    if result.get("diff"):
        lines.append("Diff:")
        lines.append(_shorten(result["diff"], limit=1200))
    return "\n".join(lines)


def _find_request(token):
    return RemoteChangeRequest.objects.filter(approval_token=token).first()


def _find_latest_pending_request(chat_id, session=None):
    if session and session.last_pending_token:
        request_obj = RemoteChangeRequest.objects.filter(
            approval_token=session.last_pending_token,
            chat_id=chat_id,
            status=RemoteChangeRequest.STATUS_PENDING,
        ).first()
        if request_obj:
            return request_obj

    return RemoteChangeRequest.objects.filter(
        chat_id=chat_id,
        status=RemoteChangeRequest.STATUS_PENDING,
    ).first()


def _calendar_timezone():
    try:
        return ZoneInfo(settings.FEISHU_CALENDAR_TIMEZONE)
    except ZoneInfoNotFoundError as exc:
        raise FeishuConfigError(
            f"FEISHU_CALENDAR_TIMEZONE is invalid: {settings.FEISHU_CALENDAR_TIMEZONE}"
        ) from exc


def _format_timestamp(value, timezone_name=None):
    if not value:
        return ""
    tz = _calendar_timezone()
    source_timezone = timezone_name or settings.FEISHU_CALENDAR_TIMEZONE
    try:
        source_tz = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError:
        source_tz = tz
    dt_value = datetime.fromtimestamp(int(value), source_tz).astimezone(tz)
    return dt_value.strftime("%Y-%m-%d %H:%M")


def _event_sort_key(event):
    start_time = event.get("start_time") or {}
    if start_time.get("timestamp"):
        return (0, int(start_time["timestamp"]))
    if start_time.get("date"):
        return (1, start_time["date"])
    return (2, event.get("event_id") or "")


def _format_event_time_range(event):
    start_time = event.get("start_time") or {}
    end_time = event.get("end_time") or {}

    if start_time.get("date") and end_time.get("date"):
        return f"{start_time['date']} 全天"

    start_text = _format_timestamp(start_time.get("timestamp"), start_time.get("timezone"))
    end_text = _format_timestamp(end_time.get("timestamp"), end_time.get("timezone"))
    if start_text and end_text:
        if start_text[:10] == end_text[:10]:
            return f"{start_text} - {end_text[11:]}"
        return f"{start_text} - {end_text}"
    return "时间未知"


def _format_calendar_create_usage():
    return (
        "用法:\n"
        "/calendar create 2026-03-15 14:00 15:00 标题 | 可选描述\n"
        "/calendar list [YYYY-MM-DD]\n"
        "/calendar delete <event_id>"
    )


def _parse_calendar_command(text):
    help_match = CALENDAR_HELP_PATTERN.match(text)
    if help_match:
        return {"action": "help"}

    create_match = CALENDAR_CREATE_PATTERN.match(text)
    if create_match:
        raw_title = create_match.group(6).strip()
        title, _, description = raw_title.partition("|")
        if not title.strip():
            raise ValueError("日程标题不能为空。")
        try:
            target_day = date.fromisoformat(create_match.group(3))
            start_clock = time.fromisoformat(create_match.group(4))
            end_clock = time.fromisoformat(create_match.group(5))
        except ValueError as exc:
            raise ValueError("日期或时间格式不正确。") from exc

        start_dt = datetime.combine(target_day, start_clock, tzinfo=_calendar_timezone())
        end_dt = datetime.combine(target_day, end_clock, tzinfo=_calendar_timezone())
        if end_dt <= start_dt:
            raise ValueError("结束时间必须晚于开始时间。")

        return {
            "action": "create",
            "summary": title.strip(),
            "description": description.strip(),
            "start_dt": start_dt,
            "end_dt": end_dt,
        }

    list_match = CALENDAR_LIST_PATTERN.match(text)
    if list_match:
        target_day = date.today()
        if list_match.group(2).lower() != "today":
            target_day = datetime.now(_calendar_timezone()).date()
        if list_match.group(3):
            try:
                target_day = date.fromisoformat(list_match.group(3))
            except ValueError as exc:
                raise ValueError("日程查询日期格式不正确。") from exc
        return {"action": "list", "target_day": target_day}

    delete_match = CALENDAR_DELETE_PATTERN.match(text)
    if delete_match:
        return {"action": "delete", "event_id": delete_match.group(3)}

    if CALENDAR_PREFIX_PATTERN.match(text):
        return {"action": "invalid"}
    return None


def _handle_calendar_create(chat_id, command, sender_open_id, session=None):
    start_timestamp = int(command["start_dt"].timestamp())
    end_timestamp = int(command["end_dt"].timestamp())
    event = create_calendar_event(
        summary=command["summary"],
        description=command["description"],
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        timezone=settings.FEISHU_CALENDAR_TIMEZONE,
        attendee_open_id=sender_open_id,
    )
    time_text = _format_event_time_range(event) if event.get("start_time") else (
        f"{command['start_dt'].strftime('%Y-%m-%d %H:%M')} - {command['end_dt'].strftime('%H:%M')}"
    )
    lines = [
        f"已创建日程: {event.get('summary') or command['summary']}",
        f"时间: {time_text}",
        f"日程ID: {event['event_id']}",
    ]
    if sender_open_id and settings.FEISHU_CALENDAR_AUTO_INVITE_SENDER:
        lines.append("已自动把你加入参与人。")
    app_link = event.get("app_link") or event.get("html_link")
    if app_link:
        lines.append(f"链接: {app_link}")
    _send_session_message(chat_id, "\n".join(lines), session=session, last_mode="calendar")


def _handle_calendar_list(chat_id, command, session=None):
    start_dt = datetime.combine(command["target_day"], time.min, tzinfo=_calendar_timezone())
    end_dt = start_dt + timedelta(days=1)
    result = list_calendar_events(
        start_timestamp=int(start_dt.timestamp()),
        end_timestamp=int(end_dt.timestamp()),
    )
    items = sorted(result["items"], key=_event_sort_key)
    if not items:
        _send_session_message(
            chat_id,
            f"{command['target_day'].isoformat()} 没有查到日程。",
            session=session,
            last_mode="calendar",
        )
        return

    lines = [f"{command['target_day'].isoformat()} 日程列表:"]
    for index, event in enumerate(items[: settings.FEISHU_CALENDAR_LIST_PAGE_SIZE], start=1):
        summary = event.get("summary") or "未命名日程"
        event_id = event.get("event_id") or event.get("id") or "-"
        lines.append(f"{index}. {_format_event_time_range(event)} | {summary} | {event_id}")
    _send_session_message(chat_id, "\n".join(lines), session=session, last_mode="calendar")


def _handle_calendar_delete(chat_id, command, session=None):
    delete_calendar_event(command["event_id"])
    _send_session_message(
        chat_id,
        f"已删除日程 {command['event_id']}。",
        session=session,
        last_mode="calendar",
    )


def _handle_calendar_command(chat_id, text, sender_open_id, session=None):
    try:
        command = _parse_calendar_command(text)
    except ValueError as exc:
        _send_session_message(
            chat_id,
            f"{exc}\n\n{_format_calendar_create_usage()}",
            session=session,
            last_mode="calendar",
        )
        return True

    if command is None:
        return False

    if command["action"] in {"help", "invalid"}:
        _send_session_message(chat_id, CALENDAR_HELP_TEXT, session=session, last_mode="calendar")
        return True

    try:
        if command["action"] == "create":
            _handle_calendar_create(chat_id, command, sender_open_id, session=session)
        elif command["action"] == "list":
            _handle_calendar_list(chat_id, command, session=session)
        elif command["action"] == "delete":
            _handle_calendar_delete(chat_id, command, session=session)
        else:
            _send_session_message(chat_id, CALENDAR_HELP_TEXT, session=session, last_mode="calendar")
    except (FeishuConfigError, FeishuRequestError) as exc:
        _send_session_message(chat_id, f"日程操作失败: {exc}", session=session, last_mode="calendar")
    return True


def _handle_approve(chat_id, token, session=None):
    request_obj = _find_request(token)
    if not request_obj:
        _send_session_message(
            chat_id,
            f"未找到审批码 {token}。",
            session=session,
            last_mode="repo",
            last_pending_token="",
        )
        return
    if request_obj.status != RemoteChangeRequest.STATUS_PENDING:
        _send_session_message(
            chat_id,
            f"审批码 {token} 当前状态是 {request_obj.status}。",
            session=session,
            last_mode="repo",
            last_pending_token="",
        )
        return

    executor = RemoteExecutor()
    request_obj.status = RemoteChangeRequest.STATUS_APPROVED
    request_obj.save(update_fields=["status", "updated_at"])

    try:
        result = apply_change_plan(executor, request_obj.plan)
    except (
        RemoteExecutorConfigError,
        RemoteExecutorError,
        RemotePlanningError,
        OpenAIConfigError,
        OpenAIRequestError,
    ) as exc:
        request_obj.status = RemoteChangeRequest.STATUS_FAILED
        request_obj.execution_log = str(exc)
        request_obj.save(update_fields=["status", "execution_log", "updated_at"])
        _send_session_message(
            chat_id,
            f"执行失败: {exc}",
            session=session,
            last_mode="repo",
            last_pending_token="",
        )
        return

    request_obj.status = RemoteChangeRequest.STATUS_APPLIED
    request_obj.execution_log = json.dumps(result, ensure_ascii=False)
    request_obj.save(update_fields=["status", "execution_log", "updated_at"])
    _send_session_message(
        chat_id,
        _format_execution_result(result),
        session=session,
        last_mode="repo",
        last_pending_token="",
    )


def _handle_reject(chat_id, token, session=None):
    request_obj = _find_request(token)
    if not request_obj:
        _send_session_message(
            chat_id,
            f"未找到审批码 {token}。",
            session=session,
            last_mode="repo",
            last_pending_token="",
        )
        return
    request_obj.status = RemoteChangeRequest.STATUS_REJECTED
    request_obj.save(update_fields=["status", "updated_at"])
    _send_session_message(
        chat_id,
        f"已取消审批码 {token}。",
        session=session,
        last_mode="repo",
        last_pending_token="",
    )


def _handle_status(chat_id, token, session=None):
    request_obj = _find_request(token)
    if not request_obj:
        _send_session_message(chat_id, f"未找到审批码 {token}。", session=session, last_mode="repo")
        return
    message = [
        f"审批码: {request_obj.approval_token}",
        f"状态: {request_obj.status}",
        f"需求: {_shorten(request_obj.prompt, limit=300)}",
    ]
    if request_obj.execution_log:
        message.append(_shorten(request_obj.execution_log, limit=800))
    _send_session_message(chat_id, "\n".join(message), session=session, last_mode="repo")


def _handle_new_task(chat_id, message_id, text, sender_open_id, session=None, history=None):
    executor = RemoteExecutor()
    try:
        read_only_reply = answer_read_only_request(executor, text)
    except (
        RemoteExecutorConfigError,
        RemoteExecutorError,
        RemotePlanningError,
        OpenAIConfigError,
        OpenAIRequestError,
    ) as exc:
        _send_session_message(chat_id, f"查询失败: {exc}", session=session, last_mode="repo")
        return

    if read_only_reply:
        _send_session_message(chat_id, read_only_reply, session=session, last_mode="repo")
        return

    route = classify_user_request(text, history=history)
    cleaned_text = (route.get("message") or "").strip()
    mode = route.get("mode")
    logger.warning(
        "Feishu route selected: mode=%s reason=%s message=%r",
        mode,
        route.get("reason", ""),
        cleaned_text[:200],
    )

    if not cleaned_text:
        _send_session_message(
            chat_id,
            "请输入内容。普通问答可直接发送，仓库任务可用 /repo 开头。",
            session=session,
        )
        return

    if mode == "chat":
        try:
            answer = answer_general_question(cleaned_text, history=history)
        except (OpenAIConfigError, OpenAIRequestError, RemotePlanningError) as exc:
            _send_session_message(chat_id, f"问答失败: {exc}", session=session, last_mode="chat")
            return
        _send_session_message(chat_id, answer, session=session, last_mode="chat")
        return

    reply_text(message_id, "已收到，正在处理。")
    try:
        plan = build_change_request_plan(executor, cleaned_text, history=history)
    except (
        FeishuError,
        RemoteExecutorConfigError,
        RemoteExecutorError,
        RemotePlanningError,
        OpenAIConfigError,
        OpenAIRequestError,
    ) as exc:
        _send_session_message(chat_id, f"分析失败: {exc}", session=session, last_mode="repo")
        return

    if not plan.get("edits"):
        _send_session_message(
            chat_id,
            plan.get("reply") or plan.get("summary") or "没有生成代码变更。",
            session=session,
            last_mode="repo",
        )
        return

    risk = assess_plan_risk(cleaned_text, plan)
    plan["risks"] = _merge_notes(plan.get("risks", []), risk.get("reasons", []))

    if not risk.get("requires_confirmation"):
        try:
            result = apply_change_plan(executor, plan)
        except (
            RemoteExecutorConfigError,
            RemoteExecutorError,
            RemotePlanningError,
            OpenAIConfigError,
            OpenAIRequestError,
        ) as exc:
            _send_session_message(
                chat_id,
                f"执行失败: {exc}",
                session=session,
                last_mode="repo",
                last_pending_token="",
            )
            return

        _send_session_message(
            chat_id,
            _format_execution_result(result),
            session=session,
            last_mode="repo",
            last_pending_token="",
            memory_updates={"last_risk_level": "low"},
        )
        return

    request_obj = RemoteChangeRequest.objects.create(
        source_message_id=message_id,
        chat_id=chat_id,
        user_open_id=sender_open_id,
        prompt=cleaned_text,
        plan=plan,
    )
    _send_session_message(
        chat_id,
        format_plan_for_user(plan, request_obj.approval_token),
        session=session,
        last_mode="repo",
        last_pending_token=request_obj.approval_token,
        memory_updates={"last_risk_level": risk.get("level", "high")},
    )


def process_feishu_event(payload):
    header = payload.get("header") or {}
    logger.warning(
        "Feishu callback received: type=%s event_type=%s event_id=%s",
        payload.get("type"),
        header.get("event_type"),
        header.get("event_id"),
    )
    event_id = header.get("event_id")
    if event_id and not cache.add(_event_cache_key(event_id), "1", timeout=3600):
        logger.warning("Feishu callback ignored as duplicate: event_id=%s", event_id)
        return

    if header.get("event_type") != "im.message.receive_v1":
        logger.warning("Feishu callback ignored: unsupported event_type=%s", header.get("event_type"))
        return

    event = payload.get("event") or {}
    sender = event.get("sender") or {}
    if sender.get("sender_type") != "user":
        logger.warning("Feishu callback ignored: sender_type=%s", sender.get("sender_type"))
        return

    message = event.get("message") or {}
    chat_id = message.get("chat_id", "")
    chat_type = message.get("chat_type", "")
    if (
        chat_type != "p2p"
        and settings.FEISHU_REQUIRE_GROUP_MENTION
        and not message.get("mentions")
    ):
        logger.warning("Feishu callback ignored: group message without mention chat_id=%s", chat_id)
        return

    text = _extract_text_message(event)
    logger.warning(
        "Feishu message parsed: chat_type=%s chat_id=%s message_id=%s text=%r",
        chat_type,
        chat_id,
        message.get("message_id", ""),
        text[:200],
    )
    if not text:
        logger.warning("Feishu callback ignored: empty or unsupported message body")
        return

    sender_open_id = ((sender.get("sender_id") or {}).get("open_id") or "")
    session = _get_chat_session(chat_id, sender_open_id)
    history = _normalize_session_history(session.history if session else [])
    _append_session_turn(session, "user", text)

    if text == "/help" or (text in {"help", "帮助"} and not _terminal_has_active_passthrough(session)):
        _send_session_message(chat_id, HELP_TEXT, session=session, last_mode="chat")
        return

    if _handle_terminal_command(chat_id, text, sender_open_id, session=session):
        return

    if _handle_calendar_command(chat_id, text, sender_open_id, session=session):
        return

    if _handle_terminal_passthrough(chat_id, text, sender_open_id, session=session):
        return

    approval_match = APPROVE_PATTERN.match(text)
    if approval_match:
        _send_session_message(chat_id, f"开始执行审批码 {approval_match.group(2)}。", session=session, last_mode="repo")
        _handle_approve(chat_id, approval_match.group(2).lower(), session=session)
        return

    confirm_match = CONFIRM_PATTERN.match(text)
    if confirm_match:
        latest_request = _find_latest_pending_request(chat_id, session=session)
        if latest_request:
            _send_session_message(
                chat_id,
                f"开始执行最近的审批码 {latest_request.approval_token}。",
                session=session,
                last_mode="repo",
            )
            _handle_approve(chat_id, latest_request.approval_token, session=session)
            return
        _send_session_message(chat_id, "当前没有待执行的审批任务。", session=session, last_mode="repo")
        return

    reject_match = REJECT_PATTERN.match(text)
    if reject_match:
        _handle_reject(chat_id, reject_match.group(2).lower(), session=session)
        return

    decline_match = DECLINE_PATTERN.match(text)
    if decline_match:
        latest_request = _find_latest_pending_request(chat_id, session=session)
        if latest_request:
            _handle_reject(chat_id, latest_request.approval_token, session=session)
            return
        _send_session_message(chat_id, "当前没有待取消的审批任务。", session=session, last_mode="repo")
        return

    status_match = STATUS_PATTERN.match(text)
    if status_match:
        _handle_status(chat_id, status_match.group(2).lower(), session=session)
        return

    _handle_new_task(
        chat_id=chat_id,
        message_id=message.get("message_id", ""),
        text=text,
        sender_open_id=sender_open_id,
        session=session,
        history=history,
    )


def start_event_processing(payload):
    worker = threading.Thread(target=_safe_process_event, args=(payload,), daemon=True)
    worker.start()


def _safe_process_event(payload):
    try:
        process_feishu_event(payload)
    except FeishuError:
        logger.exception("Feishu messaging failed while processing callback.")
    except Exception:
        logger.exception("Unhandled error while processing Feishu callback.")


@csrf_exempt
@require_POST
def feishu_events(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"code": 400, "msg": "invalid json"}, status=400)

    if not _is_valid_callback(payload):
        return HttpResponseForbidden("invalid token")

    if payload.get("type") == "url_verification":
        return JsonResponse({"challenge": payload.get("challenge", "")})

    start_event_processing(payload)
    return JsonResponse({"code": 0})
