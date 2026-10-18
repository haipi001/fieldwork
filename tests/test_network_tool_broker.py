"""Real scoped broker requests; no substituted transport or target responses."""
import base64
import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
import final_core
from network_tool_broker import scanner_broker
from tests.test_final import client

@pytest.fixture
def local_target():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = b'\x00\xff\x80fixture binary bytes' if self.path == '/allowed/binary' else b'fixture actual bytes'
            self.send_response(302 if self.path == '/allowed/redirect' else 200)
            if self.path == '/allowed/redirect':
                self.send_header('Location', '/forbidden')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def prepare(client, origin, limit=3):
    project = client.post('/api/v1/engagements', json={'name': 'Broker fixture', 'target': origin,
        'mode': 'traditional', 'scope': {'allow_private_ips': True, 'allowed_targets': [origin + '/allowed']},
        'policy': {'max_requests_per_second': 100}}).json()
    client.post(f"/api/v1/engagements/{project['id']}/confirm")
    run = client.post(f"/api/v1/engagements/{project['id']}/start").json()
    with final_core.connect() as db:
        db.execute('UPDATE run_budgets_v2 SET request_limit=? WHERE run_id=?', (limit, run['id']))
    return run['id'], project['id']


def request(port, url, method='GET', token=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
    try:
        headers = {'Proxy-Authorization': 'Basic ' + base64.b64encode(('fieldwork:' + token).encode()).decode()} if token else {}
        connection.request(method, url, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader('Location')
    finally:
        connection.close()


def test_broker_scope_redirect_and_budget_use_actual_requests(client, local_target):
    origin, seen = local_target
    run_id, _ = prepare(client, origin, limit=2)
    with scanner_broker(run_id, 20) as (port, state):
        assert request(port, origin + '/allowed', token=state['_token'])[0:2] == (200, b'fixture actual bytes'), state['errors']
        assert request(port, origin + '/forbidden', token=state['_token'])[0] == 403
        assert request(port, origin + '/allowed/redirect', token=state['_token'])[0] == 302
        assert request(port, origin + '/forbidden', token=state['_token'])[0] == 403
        assert request(port, origin + '/allowed', token=state['_token'])[0] == 403
        assert state['requests'] == state['responses'] == 2
    assert seen == ['/allowed', '/allowed/redirect']
    with final_core.connect() as db:
        assert db.execute('SELECT requests_used FROM run_budgets_v2 WHERE run_id=?', (run_id,)).fetchone()[0] == 2


@pytest.mark.parametrize('mutation', ['pause', 'scope', 'policy'])
def test_broker_rechecks_authority_before_every_connection(client, local_target, mutation):
    origin, seen = local_target
    run_id, project_id = prepare(client, origin)
    with scanner_broker(run_id, 20) as (port, state):
        assert request(port, origin + '/allowed', token=state['_token'])[0] == 200, state['errors']
        with final_core.connect() as db:
            if mutation == 'pause':
                db.execute("UPDATE analysis_runs SET status='paused' WHERE id=?", (run_id,))
            else:
                column = 'current_scope_snapshot_id' if mutation == 'scope' else 'current_policy_id'
                db.execute(f'UPDATE engagements_v2 SET {column}=? WHERE id=?', ('changed', project_id))
        assert request(port, origin + '/allowed', token=state['_token'])[0] == 403
        assert state['responses'] == 1
    assert seen == ['/allowed']


def test_broker_refuses_tunnels_writes_and_other_ports(client, local_target):
    origin, seen = local_target
    run_id, _ = prepare(client, origin)
    with scanner_broker(run_id, 20) as (port, state):
        assert request(port, origin + '/allowed')[0] == 407
        assert request(port, origin + '/allowed', 'POST', token=state['_token'])[0] == 403
        assert request(port, 'example.test:443', 'CONNECT', token=state['_token'])[0] == 403
        assert request(port, 'http://127.0.0.1:1/allowed', token=state['_token'])[0] == 403
        assert state['requests'] == state['responses'] == 0
    assert seen == []


@pytest.mark.parametrize('path', ['/allowed/../forbidden', '/allowed/%2e%2e/forbidden', '/allowed/%252e%252e/forbidden', '/allowed%2fforbidden'])
def test_broker_rejects_ambiguous_path_encodings(client, local_target, path):
    origin, seen = local_target
    run_id, _ = prepare(client, origin)
    with scanner_broker(run_id, 20) as (port, state):
        assert request(port, origin + path, token=state['_token'])[0] == 403
        assert state['requests'] == 0
    assert seen == []


def test_sandbox_can_connect_only_to_selected_broker_port(client, local_target, tmp_path):
    import sys
    from isolated_execution import run_isolated
    origin, seen = local_target
    run_id, _ = prepare(client, origin)
    script = tmp_path / 'probe.py'
    target_port = int(origin.rsplit(':', 1)[1])
    with scanner_broker(run_id, 20) as (port, state):
        script.write_text(f'''import socket
s=socket.create_connection(('127.0.0.1',{port}),timeout=1)
s.close()
print('BROKER_CONNECTED')
try:
 s=socket.create_connection(('127.0.0.1',{target_port}),timeout=1)
 s.close()
 print('DIRECT_TARGET_CONNECTED')
except PermissionError:
 print('DIRECT_TARGET_DENIED')
''')
        result = run_isolated([sys.executable, '-I', 'probe.py'], tmp_path, broker_port=port)
        assert result.returncode == 0, result.stderr
        assert 'BROKER_CONNECTED' in result.stdout
        assert 'DIRECT_TARGET_DENIED' in result.stdout
        assert 'DIRECT_TARGET_CONNECTED' not in result.stdout
    assert seen == []


@pytest.mark.parametrize('trusted', [False, True])
def test_https_tunnel_preserves_path_scope_and_verifies_upstream_certificate(client, trusted):
    import ssl
    from network_broker_tls import broker_authority
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def log_message(self, *args):
            pass
    with broker_authority() as (upstream_ca, context):
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        upstream.socket = context('127.0.0.1').wrap_socket(upstream.socket, server_side=True)
        worker = threading.Thread(target=upstream.serve_forever, daemon=True)
        worker.start()
        try:
            origin = f'https://127.0.0.1:{upstream.server_port}'
            run_id, _ = prepare(client, origin)
            if trusted:
                with final_core.connect() as db:
                    row = db.execute('SELECT scope_snapshot_id FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
                    scope_row = db.execute('SELECT rules FROM scope_snapshots WHERE id=?', (row[0],)).fetchone()
                    scope = final_core.load(scope_row[0], {})
                    scope['http_tls_trust'] = {origin: upstream_ca.decode()}
                    db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?', (final_core.dump(scope), row[0]))
            with scanner_broker(run_id, 20) as (port, state):
                def through_tls(path):
                    trust = ssl.create_default_context(cadata=state['_ca_pem'].decode())
                    connection = http.client.HTTPSConnection('127.0.0.1', port, context=trust, timeout=15)
                    authorization = 'Basic ' + base64.b64encode(('fieldwork:' + state['_token']).encode()).decode()
                    connection.set_tunnel('127.0.0.1', upstream.server_port, {'Proxy-Authorization': authorization})
                    try:
                        connection.request('GET', path)
                        result = connection.getresponse()
                        result.read()
                        return result.status
                    finally:
                        connection.close()
                assert through_tls('/forbidden') == 403
                assert state['requests'] == 0
                assert through_tls('/allowed') == (200 if trusted else 403)
                assert state['tls_sessions'] == 2
                assert state['requests'] == 1 and state['responses'] == int(trusted)
                if not trusted:
                    assert 'scanner_request_rejected' in state['errors']
            assert seen == (['/allowed'] if trusted else [])
        finally:
            upstream.shutdown()
            upstream.server_close()
            worker.join(timeout=3)


def test_broker_preserves_binary_response_bytes(client, local_target):
    origin, seen = local_target
    run_id, _ = prepare(client, origin)
    with scanner_broker(run_id, 20) as (port, state):
        status, body, _ = request(port, origin + '/allowed/binary', token=state['_token'])
        assert status == 200
        assert body == b'\x00\xff\x80fixture binary bytes'
        assert state['responses'] == 1
    assert seen == ['/allowed/binary']
