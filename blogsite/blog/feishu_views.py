import json
import logging
import re
import threading

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .feishu_client import FeishuError, reply_text, send_text
from .models import RemoteChangeRequest
from .openai_client import OpenAIConfigError, OpenAIRequestError
from .remote_agent import (
    RemotePlanningError,
    apply_change_plan,
    answer_general_question,
    build_change_request_plan,
    classify_user_request,
    format_plan_for_user,
)
from .remote_executor import RemoteExecutor, RemoteExecutorConfigError, RemoteExecutorError

logger = logging.getLogger(__name__)

APPROVE_PATTERN = re.compile(r"^/(approve|批准)\s+([0-9a-f]{8})$", re.IGNORECASE)
REJECT_PATTERN = re.compile(r"^/(reject|拒绝)\s+([0-9a-f]{8})$", re.IGNORECASE)
STATUS_PATTERN = re.compile(r"^/(status|状态)\s+([0-9a-f]{8})$", re.IGNORECASE)

HELP_TEXT = """linuxclaw bot commands:
/help
/chat <question>
/repo <request>
/approve <token>
/reject <token>
/status <token>

直接发送自然语言也可以，我会自动判断模式：
- 普通问答：直接回答
- 仓库/服务器任务：先查看项目，再生成计划，等你审批

示例：
- /chat 你和 openclaw 有什么区别？
- /repo 帮我检查首页报错原因
- 帮我修改 Django 路由并补一个测试"""


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
    return f"feishu:event:{event_id}"


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


def _handle_approve(chat_id, token):
    request_obj = _find_request(token)
    if not request_obj:
        _send_chat_message(chat_id, f"未找到审批码 {token}。")
        return
    if request_obj.status != RemoteChangeRequest.STATUS_PENDING:
        _send_chat_message(chat_id, f"审批码 {token} 当前状态是 {request_obj.status}。")
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
        _send_chat_message(chat_id, f"执行失败: {exc}")
        return

    request_obj.status = RemoteChangeRequest.STATUS_APPLIED
    request_obj.execution_log = json.dumps(result, ensure_ascii=False)
    request_obj.save(update_fields=["status", "execution_log", "updated_at"])
    _send_chat_message(chat_id, _format_execution_result(result))


def _handle_reject(chat_id, token):
    request_obj = _find_request(token)
    if not request_obj:
        _send_chat_message(chat_id, f"未找到审批码 {token}。")
        return
    request_obj.status = RemoteChangeRequest.STATUS_REJECTED
    request_obj.save(update_fields=["status", "updated_at"])
    _send_chat_message(chat_id, f"已取消审批码 {token}。")


def _handle_status(chat_id, token):
    request_obj = _find_request(token)
    if not request_obj:
        _send_chat_message(chat_id, f"未找到审批码 {token}。")
        return
    message = [
        f"审批码: {request_obj.approval_token}",
        f"状态: {request_obj.status}",
        f"需求: {_shorten(request_obj.prompt, limit=300)}",
    ]
    if request_obj.execution_log:
        message.append(_shorten(request_obj.execution_log, limit=800))
    _send_chat_message(chat_id, "\n".join(message))


def _handle_new_task(chat_id, message_id, text, sender_open_id):
    route = classify_user_request(text)
    cleaned_text = (route.get("message") or "").strip()
    mode = route.get("mode")
    logger.warning(
        "Feishu route selected: mode=%s reason=%s message=%r",
        mode,
        route.get("reason", ""),
        cleaned_text[:200],
    )

    if not cleaned_text:
        _send_chat_message(chat_id, "请输入内容。普通问答可直接发送，仓库任务可用 /repo 开头。")
        return

    if mode == "chat":
        try:
            answer = answer_general_question(cleaned_text)
        except (OpenAIConfigError, OpenAIRequestError, RemotePlanningError) as exc:
            _send_chat_message(chat_id, f"问答失败: {exc}")
            return
        _send_chat_message(chat_id, answer)
        return

    reply_text(message_id, "已收到，正在查看项目并生成修改计划。")
    executor = RemoteExecutor()
    try:
        plan = build_change_request_plan(executor, cleaned_text)
    except (
        FeishuError,
        RemoteExecutorConfigError,
        RemoteExecutorError,
        RemotePlanningError,
        OpenAIConfigError,
        OpenAIRequestError,
    ) as exc:
        _send_chat_message(chat_id, f"分析失败: {exc}")
        return

    if not plan.get("edits"):
        _send_chat_message(chat_id, plan.get("reply") or plan.get("summary") or "没有生成代码变更。")
        return

    request_obj = RemoteChangeRequest.objects.create(
        source_message_id=message_id,
        chat_id=chat_id,
        user_open_id=sender_open_id,
        prompt=cleaned_text,
        plan=plan,
    )
    _send_chat_message(chat_id, format_plan_for_user(plan, request_obj.approval_token))


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

    if text in {"/help", "help", "帮助"}:
        reply_text(message.get("message_id"), HELP_TEXT)
        return

    approval_match = APPROVE_PATTERN.match(text)
    if approval_match:
        _send_chat_message(chat_id, f"开始执行审批码 {approval_match.group(2)}。")
        _handle_approve(chat_id, approval_match.group(2).lower())
        return

    reject_match = REJECT_PATTERN.match(text)
    if reject_match:
        _handle_reject(chat_id, reject_match.group(2).lower())
        return

    status_match = STATUS_PATTERN.match(text)
    if status_match:
        _handle_status(chat_id, status_match.group(2).lower())
        return

    _handle_new_task(
        chat_id=chat_id,
        message_id=message.get("message_id", ""),
        text=text,
        sender_open_id=((sender.get("sender_id") or {}).get("open_id") or ""),
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
