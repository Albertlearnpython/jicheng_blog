from urllib.parse import urlparse, urlsplit

from channels.security.websocket import OriginValidator, WebsocketDenier
from django.conf import settings


class FeishuCompatibleOriginValidator(OriginValidator):
    def __init__(self, application, allowed_origins, allow_missing_origin=True, allow_null_origin=True):
        super().__init__(application, allowed_origins)
        self.allow_missing_origin = allow_missing_origin
        self.allow_null_origin = allow_null_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            raise ValueError("You cannot use FeishuCompatibleOriginValidator on a non-WebSocket connection")

        origin_value = ""
        parsed_origin = None
        for header_name, header_value in scope.get("headers", []):
            if header_name != b"origin":
                continue
            try:
                origin_value = header_value.decode("latin1").strip()
            except UnicodeDecodeError:
                origin_value = ""
            if origin_value and origin_value.lower() != "null":
                parsed_origin = urlparse(origin_value)
            break

        if self.valid_origin(parsed_origin, origin_value):
            return await self.application(scope, receive, send)

        denier = WebsocketDenier()
        return await denier(scope, receive, send)

    def valid_origin(self, parsed_origin, origin_value=""):
        normalized_origin = (origin_value or "").strip().lower()
        if not normalized_origin:
            return self.allow_missing_origin or "*" in self.allowed_origins
        if normalized_origin == "null":
            return self.allow_null_origin
        return self.validate_origin(parsed_origin)


def terminal_origin_validator(application):
    allowed_hosts = list(settings.ALLOWED_HOSTS)
    if settings.DEBUG and not allowed_hosts:
        allowed_hosts = ["localhost", "127.0.0.1", "[::1]"]

    allowed_origins = allowed_hosts + list(getattr(settings, "TERMINAL_WEBSOCKET_ALLOWED_ORIGINS", []))
    return FeishuCompatibleOriginValidator(application, allowed_origins)


class NormalizeWebSocketPathMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            raw_path = (scope.get("path") or "").strip()
            if raw_path.startswith("ws://") or raw_path.startswith("wss://"):
                parsed = urlsplit(raw_path)
                normalized_scope = dict(scope)
                normalized_scope["path"] = parsed.path or "/"
                if parsed.query:
                    normalized_scope["query_string"] = parsed.query.encode("utf-8")
                scope = normalized_scope
        return await self.application(scope, receive, send)
