import json
import logging
import re
import threading
from contextlib import contextmanager

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .codex_client import CodexConfigError, CodexExecutionError, CodexSSHClient
from .feishu_client import FeishuConfigError, FeishuError, FeishuRequestError, reply_text, send_text
from .models import FeishuChatSession
from .translation_client import maybe_translate_user_message

logger = logging.getLogger(__name__)

_CHAT_LOCKS = {}
_CHAT_LOCKS_GUARD = threading.Lock()


def _event_cache_key(event_id):
    return f"feishu:event:{event_id}"


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
    mentions = message.get("mentions") or []
    for mention in mentions:
        name = (mention.get("name") or "").strip()
        if not name:
            continue
        pattern = rf"^\s*@{re.escape(name)}(?:\s+|$)"
        text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)

    return text.strip()


def _message_chunks(text, limit=3500):
    cleaned = (text or "").strip()
    if not cleaned:
        return ["Codex did not return any content."]

    if len(cleaned) <= limit:
        return [cleaned]

    chunks = []
    remaining = cleaned
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 3:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if not chunk:
            chunk = remaining[:limit].strip()
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _send_chat_reply(chat_id, message_id, text):
    chunks = _message_chunks(text)
    reply_text(message_id, chunks[0])
    for chunk in chunks[1:]:
        send_text(chat_id, chunk)


def _get_or_create_session(chat_id, user_open_id):
    session, _ = FeishuChatSession.objects.get_or_create(
        chat_id=chat_id,
        defaults={"user_open_id": user_open_id},
    )
    if user_open_id and session.user_open_id != user_open_id:
        session.user_open_id = user_open_id
        session.save(update_fields=["user_open_id", "updated_at"])
    return session


@contextmanager
def _chat_lock(chat_id):
    with _CHAT_LOCKS_GUARD:
        lock = _CHAT_LOCKS.setdefault(chat_id, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def process_feishu_event(payload):
    header = payload.get("header") or {}
    event_id = header.get("event_id")
    event_type = header.get("event_type")

    logger.info("Feishu callback received: event_type=%s event_id=%s", event_type, event_id)

    if event_id and not cache.add(_event_cache_key(event_id), "1", timeout=3600):
        logger.info("Ignored duplicate Feishu callback: %s", event_id)
        return

    if event_type != "im.message.receive_v1":
        logger.info("Ignored unsupported Feishu event type: %s", event_type)
        return

    event = payload.get("event") or {}
    sender = event.get("sender") or {}
    if sender.get("sender_type") != "user":
        logger.info("Ignored non-user Feishu sender: %s", sender.get("sender_type"))
        return

    message = event.get("message") or {}
    chat_id = (message.get("chat_id") or "").strip()
    message_id = (message.get("message_id") or "").strip()
    chat_type = (message.get("chat_type") or "").strip()

    if not chat_id or not message_id:
        logger.warning("Ignored Feishu callback without chat_id or message_id.")
        return

    if chat_type != "p2p" and settings.FEISHU_REQUIRE_GROUP_MENTION and not message.get("mentions"):
        logger.info("Ignored group message without mention: chat_id=%s", chat_id)
        return

    text = _extract_text_message(event)
    if not text:
        logger.info("Ignored empty or unsupported Feishu message body.")
        return

    sender_open_id = ((sender.get("sender_id") or {}).get("open_id") or "").strip()
    session = _get_or_create_session(chat_id, sender_open_id)

    try:
        with _chat_lock(chat_id):
            session.refresh_from_db()
            codex = CodexSSHClient()
            translated_text = maybe_translate_user_message(text)
            result = codex.run_turn(translated_text, thread_id=session.codex_thread_id)
            session.codex_thread_id = result.thread_id
            session.last_user_message = text
            session.last_assistant_message = result.reply_text
            session.save(
                update_fields=[
                    "codex_thread_id",
                    "last_user_message",
                    "last_assistant_message",
                    "updated_at",
                ]
            )
        _send_chat_reply(chat_id, message_id, result.reply_text)
    except CodexConfigError as exc:
        logger.exception("Codex configuration error while processing Feishu message.")
        _send_chat_reply(chat_id, message_id, f"Codex 配置错误：{exc}")
    except CodexExecutionError as exc:
        logger.exception("Codex execution error while processing Feishu message.")
        _send_chat_reply(chat_id, message_id, f"Codex 调用失败：{exc}")
    except (FeishuConfigError, FeishuRequestError):
        logger.exception("Failed to send Feishu reply.")


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
