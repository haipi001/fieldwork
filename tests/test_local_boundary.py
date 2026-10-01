import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse
from local_boundary import LocalBoundaryMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(LocalBoundaryMiddleware)

    @app.api_route('/api/private', methods=['GET', 'POST', 'DELETE'])
    async def private():
        return {'reached': True}

    @app.get('/download')
    async def download():
        return StreamingResponse(iter([b'evidence']))

    @app.websocket('/events')
    async def events(ws: WebSocket):
        await ws.accept()
        await ws.send_text('ready')
        await ws.close()

    return TestClient(app, base_url='http://127.0.0.1:8000')


@pytest.mark.parametrize('host', ['evil.example:8000', '127.0.0.1.evil:8000', '127.0.0.1:8001', 'localhost', 'user@127.0.0.1:8000', '127.0.0.1:8000.', '[::1]:8001'])
def test_reject_host(client, host):
    assert client.get('/api/private', headers={'host': host}).json()['code'] == 'local_host_rejected'


@pytest.mark.parametrize('origin', ['https://evil.example', 'null', 'http://localhost:8000', 'http://127.0.0.1:8001', 'http://127.0.0.1:8000/', 'https://127.0.0.1:8000'])
@pytest.mark.parametrize('method', ['GET', 'POST', 'DELETE'])
def test_reject_origin(client, origin, method):
    response = client.request(method, '/api/private', headers={'origin': origin})
    assert response.status_code == 403
    assert response.json()['code'] == 'local_origin_rejected'


@pytest.mark.parametrize('site', ['cross-site', 'same-site', 'invalid'])
def test_fetch_metadata(client, site):
    assert client.get('/download', headers={'sec-fetch-site': site}).status_code == 403


def test_duplicate_headers(client):
    for headers in [[('host', '127.0.0.1:8000'), ('host', 'evil')], [('origin', 'http://127.0.0.1:8000')] * 2]:
        assert client.get('/api/private', headers=headers).status_code == 403


@pytest.mark.parametrize('host', ['127.0.0.1:8000', 'localhost:8000', '[::1]:8000'])
def test_same_origin_and_native(client, host):
    for extra in [{}, {'origin': 'http://' + host, 'sec-fetch-site': 'same-origin'}]:
        response = client.post('/api/private', headers={'host': host, **extra})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-frame-options'] == 'DENY'


def test_download_and_preflight(client):
    assert client.get('/download').content == b'evidence'
    assert client.options('/api/private', headers={'origin': 'https://evil.example'}).status_code == 403


def test_websocket_boundary(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect('ws://127.0.0.1:8000/events', headers={'origin': 'https://evil.example'}):
            pass
    assert error.value.code == 1008
    with client.websocket_connect('ws://127.0.0.1:8000/events', headers={'origin': 'http://127.0.0.1:8000'}) as ws:
        assert ws.receive_text() == 'ready'


def test_browser_collector_origin_exception_is_exact_path_only(client):
    origin = 'chrome-extension://' + 'a' * 32
    # The collector route is allowed through the outer browser boundary; its
    # own bearer/pairing contract remains responsible for authentication.
    assert client.post('/api/v1/agent-audit/browser/events', headers={'origin': origin}).status_code == 404
    assert client.post('/api/private', headers={'origin': origin}).status_code == 403
    assert client.post('/api/v1/agent-audit/browser/events', headers={
        'origin': 'chrome-extension://' + 'z' * 32,
    }).status_code == 403


def test_custom_port():
    app = FastAPI()
    app.add_middleware(LocalBoundaryMiddleware, port=9123)
    client = TestClient(app, base_url='http://localhost:9123')
    assert client.get('/').status_code == 404
    assert client.get('/', headers={'host': 'localhost:8000'}).status_code == 403


def test_application_routes_are_guarded_without_database_startup():
    import app as application
    client = TestClient(application.app, base_url='http://127.0.0.1:8000')
    assert client.get('/health').json() == {'status': 'ready'}
    for path in ['/api/v1/engagements', '/api/engagements', '/new', '/static/final.js', '/api/v1/reports/export']:
        assert client.get(path, headers={'origin': 'https://evil.example'}).status_code == 403


def test_application_rejects_spoofed_host_even_without_origin():
    import app as application
    client = TestClient(application.app, base_url='http://127.0.0.1:8000')
    response = client.get('/health', headers={'host': 'fieldwork.attacker.test:8000'})
    assert response.status_code == 403
    assert response.json()['code'] == 'local_host_rejected'
