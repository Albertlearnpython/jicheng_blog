import json
import socket
from urllib import error, parse, request

from django.conf import settings
from django.core.cache import cache


class FeishuError(Exception):
    pass


class FeishuConfigError(FeishuError):
    pass


class FeishuRequestError(FeishuError):
    pass


def _api_url(path):
    base = settings.FEISHU_BASE_URL.rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _request_json(url, method="GET", payload=None, headers=None, timeout=None):
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, headers=request_headers, method=method.upper())

    try:
        with request.urlopen(req, timeout=timeout or settings.FEISHU_EVENT_REPLY_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise FeishuRequestError("Request to Feishu timed out.") from exc
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise FeishuRequestError(f"Feishu API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise FeishuRequestError(f"Network error while contacting Feishu: {exc.reason}") from exc


def get_tenant_access_token():
    cache_key = "feishu:tenant_access_token"
    cached = cache.get(cache_key)
    if cached:
        return cached

    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        raise FeishuConfigError("FEISHU_APP_ID or FEISHU_APP_SECRET is not configured.")

    payload = _request_json(
        _api_url("/open-apis/auth/v3/tenant_access_token/internal"),
        method="POST",
        payload={
            "app_id": settings.FEISHU_APP_ID,
            "app_secret": settings.FEISHU_APP_SECRET,
        },
    )
    if payload.get("code") != 0 or not payload.get("tenant_access_token"):
        raise FeishuRequestError(
            payload.get("msg") or "Failed to fetch Feishu tenant access token."
        )

    token = payload["tenant_access_token"]
    expire_seconds = max(int(payload.get("expire", 7200)) - 60, 60)
    cache.set(cache_key, token, expire_seconds)
    return token


def feishu_api_request(method, path, payload=None, params=None, timeout=None):
    token = get_tenant_access_token()
    url = _api_url(path)
    if params:
        query = parse.urlencode(
            {key: value for key, value in params.items() if value is not None},
            doseq=True,
        )
        if query:
            url = f"{url}?{query}"
    response_data = _request_json(
        url,
        method=method,
        payload=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if response_data.get("code") != 0:
        raise FeishuRequestError(response_data.get("msg") or "Feishu API request failed.")
    return response_data


def _send_message(path, payload):
    return feishu_api_request("POST", path, payload=payload)


def reply_text(message_id, text):
    safe_text = (text or "").strip()[:4000]
    return _send_message(
        f"/open-apis/im/v1/messages/{message_id}/reply",
        {
            "msg_type": "text",
            "content": json.dumps({"text": safe_text}, ensure_ascii=False),
        },
    )


def send_text(receive_id, text, receive_id_type="chat_id"):
    safe_text = (text or "").strip()[:4000]
    return _send_message(
        f"/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": safe_text}, ensure_ascii=False),
        },
    )
