"""Per-launch desktop/API session boundary.

The desktop supplies the token through the child process environment and installs
the same value as an HttpOnly WKWebView cookie. It must never be placed in a URL,
HTML, JavaScript storage, logs, evidence, or reports.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from starlette.responses import JSONResponse


COOKIE_NAME = "fieldwork_session"
HEADER_NAME = b"x-fieldwork-session"
PUBLIC_PATHS = {"/health"}
EXTERNAL_AUTH_PATHS = {"/api/v1/agent-audit/browser/events"}


def _configured_token() -> str:
    value = os.getenv("FIELDWORK_SESSION_TOKEN", "")
    if value:
        if not 32 <= len(value) <= 256 or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise RuntimeError("FIELDWORK_SESSION_TOKEN must be 32-256 printable ASCII characters")
        return value
    # Secure by default for direct uvicorn launches. Manual clients must set a
    # token explicitly; an unknown generated token intentionally makes the
    # business API inaccessible instead of silently exposing it.
    return secrets.token_urlsafe(48)


def _cookie_values(raw: str) -> list[str]:
    values = []
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == COOKIE_NAME:
            values.append(value)
    return values


class SessionAuthMiddleware:
    def __init__(self, app, token: str | None = None, allow_test_bypass: bool = False):
        self.app = app
        self.token = token or _configured_token()
        self.token_digest = hashlib.sha256(self.token.encode("ascii")).digest()
        self.allow_test_bypass = allow_test_bypass

    def _authorized(self, scope) -> bool:
        headers: dict[bytes, list[str]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value.decode("latin-1"))
        supplied: list[str] = []
        supplied.extend(headers.get(HEADER_NAME, []))
        for raw in headers.get(b"cookie", []):
            supplied.extend(_cookie_values(raw))
        if len(supplied) != 1:
            return False
        try:
            digest = hashlib.sha256(supplied[0].encode("ascii")).digest()
        except UnicodeEncodeError:
            return False
        return hmac.compare_digest(digest, self.token_digest)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in PUBLIC_PATHS or path in EXTERNAL_AUTH_PATHS:
            return await self.app(scope, receive, send)
        if self.allow_test_bypass and os.getenv("PYTEST_CURRENT_TEST"):
            return await self.app(scope, receive, send)
        if self._authorized(scope):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        response = JSONResponse(
            {"detail": "Fieldwork 本机会话无效，请从桌面应用重新打开。", "code": "session_required"},
            status_code=401,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        return await response(scope, receive, send)
