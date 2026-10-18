"""Local browser boundary. This is not session authentication or a sandbox."""
import re

from starlette.responses import JSONResponse


EXTENSION_EVENT_PATH = "/api/v1/agent-audit/browser/events"
CHROME_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}\Z")


class LocalBoundaryMiddleware:
    def __init__(self, app, port=8000):
        self.app = app
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("Invalid Fieldwork port")
        self.authorities = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        if port == 80:
            self.authorities.update({"127.0.0.1", "localhost", "[::1]"})

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value.decode("latin-1"))
        hosts = headers.get(b"host", [])
        origins = headers.get(b"origin", [])
        fetch_sites = headers.get(b"sec-fetch-site", [])
        reason = None
        if len(hosts) != 1 or hosts[0] not in self.authorities:
            reason = "local_host_rejected"
        elif origins:
            extension_event = (scope.get("path") == EXTENSION_EVENT_PATH and len(origins) == 1
                               and CHROME_EXTENSION_ORIGIN.fullmatch(origins[0]))
            if len(origins) != 1 or (origins[0] != "http://" + hosts[0] and not extension_event):
                reason = "local_origin_rejected"
        elif fetch_sites and (len(fetch_sites) != 1 or fetch_sites[0] not in {"same-origin", "none"}):
            reason = "cross_site_rejected"
        if reason:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            response = JSONResponse({"detail": "请求来源不受信任，请从 Fieldwork 本机窗口重新打开。", "code": reason}, status_code=403,
                                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
            return await response(scope, receive, send)

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers_out = list(message.get("headers", []))
                headers_out.extend([(b"x-content-type-options", b"nosniff"),
                                    (b"referrer-policy", b"no-referrer"),
                                    (b"x-frame-options", b"DENY"),
                                    (b"content-security-policy", b"frame-ancestors 'none'; object-src 'none'; base-uri 'self'")])
                if not scope.get("path", "").startswith("/static/"):
                    headers_out.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers_out}
            await send(message)
        await self.app(scope, receive, protected_send)
