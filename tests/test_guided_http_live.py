"""Real loopback transport acceptance, not discovery or independent confirmation."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import final_core
import guided_http
import traditional_runtime as http
from tests.test_guided_research import candidate, client


@pytest.mark.parametrize('scenario,expected', [
    ('private_vulnerable', 'reproduced'),
    ('private_fixed', 'not_established'),
    ('public_shared', 'not_established'),
    # Owner metadata alone cannot distinguish legitimate authenticated sharing.
    ('authenticated_shared', 'reproduced'),
])
def test_live_object_read_controls_do_not_self_confirm(client, monkeypatch, scenario, expected):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            identity = self.headers.get('Authorization')
            requests.append((self.path, identity))
            if self.path == '/api/me':
                status, payload = (200, {'id': 'A' if identity == 'owner' else 'B'})
            elif (not identity and scenario != 'public_shared') or (identity == 'other' and scenario == 'private_fixed'):
                status, payload = 403, {'error': 'denied'}
            else:
                status, payload = 200, {'owner_id': 'A', 'private': 'fixture-only-value'}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f'http://127.0.0.1:{server.server_port}'
        run, created = candidate(client, target=origin + '/object')
        detail = client.get(f"/api/v1/candidates/{created['id']}").json()['candidate']
        engagement = final_core.get_engagement(detail['engagement_id'])
        engagement['scope'].update(allow_authentication=True, allow_private_ips=True)
        engagement['policy']['max_requests_per_second'] = 1000
        monkeypatch.setattr(final_core, 'get_engagement', lambda _: engagement)
        # Only the fixture's authorization decision and credential provider are substituted.
        # network_guard performs real DNS/private-IP checks; request_once is unmodified.
        checks = []
        def policy_check(body):
            checks.append(body.target)
            return {'allowed': body.target in {origin + '/object', origin + '/api/me'}, 'reason': 'fixture scope'}
        monkeypatch.setattr(final_core, 'execution_policy_check', policy_check)
        monkeypatch.setattr(final_core, 'get_identity', lambda identity: dict(
            id=identity, engagement_id=detail['engagement_id'], session_status='ready', expires_at=None))
        monkeypatch.setattr(http, 'resolve_identity_headers', lambda identity: {'Authorization': identity})
        ids = [dict(id=value, session_status='ready', expires_at=None) for value in ('owner', 'other')]
        # Collect actual responses rather than inserting response answers into the oracle.
        rows = []
        for key, path, identity in [('object', '/object', 'owner'), ('me-a', '/api/me', 'owner'), ('me-b', '/api/me', 'other')]:
            result = http.request_once(http.ReplayRequest(url=origin + path, headers={'Authorization': identity}))
            rows.append(dict(id=key, url=origin + path, identity_id=identity, method='GET',
                             response_status=result['status'], response_body_preview=result['body_preview'],
                             response_sha256=result['body_sha256']))
        binding = guided_http.plan(detail, rows, ids)
        assert binding is not None
        with final_core.connect() as db:
            db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',
                       (run['id'], 20, 0, 10, 0, 0, 0, final_core.utcnow()))
        result = guided_http.execute(detail, binding, lambda *args: None)
        assert result['status'] == expected
        assert len(requests) == 13 and len(checks) == 15
        assert 'Authorization' not in json.dumps(result)
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
            assert db.execute('SELECT requests_used FROM run_budgets_v2 WHERE run_id=?', (run['id'],)).fetchone()[0] == 10
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
