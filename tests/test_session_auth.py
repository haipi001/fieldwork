import os

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from session_auth import SessionAuthMiddleware


TOKEN = "fixture-session-token-that-is-long-enough"


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware, token=TOKEN)

    @app.get("/health")
    def health():
        return {"status": "ready"}

    @app.api_route("/api/private", methods=["GET", "POST"])
    def private():
        return {"ok": True}

    @app.websocket("/events")
    async def events(ws: WebSocket):
        await ws.accept()
        await ws.send_text("ready")
        await ws.close()

    return TestClient(app)


def test_health_is_public_but_business_routes_require_session(client):
    assert client.get("/health").status_code == 200
    response = client.get("/api/private")
    assert response.status_code == 401
    assert response.json()["code"] == "session_required"
    assert response.headers["cache-control"] == "no-store"


def test_cookie_and_cli_header_are_accepted_without_echo(client):
    for headers in ({"Cookie": f"fieldwork_session={TOKEN}"}, {"X-Fieldwork-Session": TOKEN}):
        response = client.post("/api/private", headers=headers)
        assert response.status_code == 200
        assert TOKEN not in response.text


@pytest.mark.parametrize("headers", [
    {"Cookie": "fieldwork_session=wrong"},
    {"X-Fieldwork-Session": "wrong"},
    {"Cookie": f"fieldwork_session={TOKEN}", "X-Fieldwork-Session": TOKEN},
    {"Cookie": f"fieldwork_session={TOKEN}; fieldwork_session={TOKEN}"},
])
def test_invalid_ambiguous_or_replayed_shapes_are_rejected(client, headers):
    assert client.get("/api/private", headers=headers).status_code == 401


def test_websocket_requires_cookie(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/events"):
            pass
    assert error.value.code == 1008
    with client.websocket_connect("/events", headers={"Cookie": f"fieldwork_session={TOKEN}"}) as ws:
        assert ws.receive_text() == "ready"


def test_browser_collector_keeps_its_distinct_bearer_boundary(client):
    # The endpoint itself is absent in this fixture, proving only that the
    # desktop session layer does not consume the extension's bearer contract.
    assert client.post("/api/v1/agent-audit/browser/events").status_code == 404


def test_old_token_is_rejected_after_process_rotation():
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware, token="new-session-token-that-is-long-enough")

    @app.get("/api/private")
    def private():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/private", headers={
        "Cookie": "fieldwork_session=old-session-token-that-is-long-enough",
    }).status_code == 401
    assert client.get("/api/private", headers={
        "Cookie": "fieldwork_session=new-session-token-that-is-long-enough",
    }).status_code == 200


def test_invalid_environment_token_fails_closed(monkeypatch):
    from session_auth import SessionAuthMiddleware

    monkeypatch.setenv("FIELDWORK_SESSION_TOKEN", "short")
    with pytest.raises(RuntimeError, match="32-256"):
        SessionAuthMiddleware(FastAPI())


def test_application_rejects_unauthenticated_business_api_outside_test_bypass(monkeypatch):
    import app as application

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/engagements").status_code == 401


def test_desktop_contract_keeps_session_out_of_url_javascript_and_argv():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "macos" / "FieldworkApp.swift").read_text()
    assert 'environment["FIELDWORK_SESSION_TOKEN"] = sessionToken' in source
    assert '.name: "fieldwork_session"' in source
    assert '.init(rawValue: "HttpOnly"): "TRUE"' in source
    assert 'X-Fieldwork-Instance' in source and 'X-Fieldwork-Version' in source
    assert 'process.arguments = ["-m", "desktop_server"' in source
    assert 'process.standardInput = lifetime' in source
    assert 'environment["FIELDWORK_DESKTOP_PARENT_PID"]' in source
    arguments = source.split('process.arguments = ', 1)[1].split('\n', 1)[0]
    assert "sessionToken" not in arguments and "desktopInstance" not in arguments
    app_url = source.split('private var appURL:', 1)[1].split('\n', 1)[0]
    assert "session" not in app_url.lower() and "token" not in app_url.lower()
