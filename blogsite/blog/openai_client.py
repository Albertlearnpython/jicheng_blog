import json
import socket
from urllib import error, request

from django.conf import settings


class OpenAIConfigError(Exception):
    pass


class OpenAIRequestError(Exception):
    pass


def build_payload(message, reasoning_effort=None, verbosity=None):
    payload = {
        "model": settings.OPENAI_MODEL,
        "input": message,
        "instructions": settings.CHAT_SYSTEM_PROMPT,
        "store": False,
    }

    effort = reasoning_effort or settings.OPENAI_REASONING_EFFORT
    if effort and effort != "none":
        payload["reasoning"] = {"effort": effort}

    text_verbosity = verbosity or settings.OPENAI_TEXT_VERBOSITY
    if text_verbosity:
        payload["text"] = {"format": {"type": "text"}, "verbosity": text_verbosity}

    return payload


def extract_text(response_data):
    for item in response_data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"].strip()

    output_text = response_data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    raise OpenAIRequestError("API returned no assistant text.")


def create_chat_response(message, reasoning_effort=None, verbosity=None):
    if not settings.OPENAI_API_KEY:
        raise OpenAIConfigError("OPENAI_API_KEY is not configured.")

    payload = build_payload(message, reasoning_effort=reasoning_effort, verbosity=verbosity)
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
    }
    req = request.Request(settings.OPENAI_API_URL, data=body, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=settings.OPENAI_REQUEST_TIMEOUT) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise OpenAIRequestError(
            f"Request to API timed out after {settings.OPENAI_REQUEST_TIMEOUT} seconds."
        ) from exc
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenAIRequestError(f"OpenAI API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise OpenAIRequestError(
                f"Request to API timed out after {settings.OPENAI_REQUEST_TIMEOUT} seconds."
            ) from exc
        raise OpenAIRequestError(f"Network error while contacting OpenAI: {exc.reason}") from exc

    return {
        "response_id": response_data.get("id"),
        "model": response_data.get("model", settings.OPENAI_MODEL),
        "text": extract_text(response_data),
    }
