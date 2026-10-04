"""Actual HTTP replay must respect configured business sharing semantics."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import final_core
import traditional_runtime as http
from tests.test_final import client


@pytest.fixture
def object_server():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            role = self.headers.get('X-Role')
            calls.append((self.path, role))
            status = 200 if role else 403
            value = ({'id': 'A' if role == 'owner' else 'B'} if self.path == '/me'
                     else {'owner_id': 'A', 'record': 'local fixture content'}) if role else {'error': 'denied'}
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize('rule_case,expected,reason', [
    ('owner_only', 'verified', 'access_denied_by_rule'),
    ('allowlist_denied', 'verified', 'access_denied_by_rule'),
    ('shared', 'human_review', 'access_permitted_by_rule'),
    ('missing', 'human_review', 'missing_rule'),
    ('ambiguous', 'human_review', 'ambiguous_rule'),
    ('wrong_target', 'human_review', 'missing_rule'),
    ('malformed', 'human_review', 'invalid_rule'),
    ('changed', 'rejected', None),
])
def test_live_finalization_requires_business_boundary(client, object_server, rule_case, expected, reason):
    origin, calls = object_server
    target = origin + '/object'
    rule = {'target': target, 'access': 'owner_only', 'source': 'Frozen local fixture ownership specification'}
    if rule_case in ('shared', 'allowlist_denied'):
        rule.update(access='allowlist', allowed_principals=['B'] if rule_case == 'shared' else ['C'])
    if rule_case == 'wrong_target':
        rule['target'] += '/another'
    if rule_case == 'malformed':
        rule['access'] = []
    rules = [] if rule_case == 'missing' else [rule, rule] if rule_case == 'ambiguous' else [rule]
    response = client.post('/api/v1/engagements', json={
        'name': 'Business boundary fixture', 'target': origin, 'mode': 'traditional',
        'scope': {'allow_private_ips': True, 'allow_authentication': True, 'http_object_read_rules': rules},
        'policy': {'max_requests_per_second': 1000},
    })
    assert response.status_code == 201
    engagement = client.post(f"/api/v1/engagements/{response.json()['id']}/confirm").json()
    run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()
    observation = client.post(f"/api/v1/runs/{run['id']}/observations", json={
        'observation_type': 'http.authorization', 'subject': target,
        'summary': 'Local fixture object read requires replay', 'source_capability': 'fixture',
    }).json()
    candidate = client.post(f"/api/v1/runs/{run['id']}/candidates", json={
        'title': 'Local cross-identity object read', 'category': 'CWE-639', 'target': target,
        'hypothesis': 'Check configured object boundary', 'observation_ids': [observation['id']],
    }).json()
    payload = http.HttpReplayInput(
        candidate_id=candidate['id'], baseline=http.ReplayRequest(url=target, headers={'X-Role': 'owner'}),
        attack=http.ReplayRequest(url=target, headers={'X-Role': 'other'}),
        negative_control=http.ReplayRequest(url=target),
        authorization=http.AuthorizationAssertion(
            baseline_identity=http.ReplayRequest(url=origin + '/me', headers={'X-Role': 'owner'}),
            attack_identity=http.ReplayRequest(url=origin + '/me', headers={'X-Role': 'other'}),
            principal_field='id', owner_field='owner_id'),
        severity='high', impact_description='Fixture record disclosure outside the configured permission',
        root_cause='Fixture missing object authorization', weakness='CWE-639', location=target)
    preview = http.preview_http_replay(run['id'], payload)
    assert preview['can_prove'] == (rule_case in ('owner_only', 'allowlist_denied', 'shared', 'changed'))
    assert calls == []
    def after_response():
        if rule_case == 'changed' and len(calls) == 10:
            with final_core.connect() as db:
                scope = {**engagement['scope'], 'http_object_read_rules': []}
                db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',
                           (final_core.dump(scope), engagement['current_scope_snapshot_id']))
    if expected == 'rejected':
        from fastapi import HTTPException
        with pytest.raises(HTTPException, match='业务权限规则已变化'):
            http.execute_http_replay(run['id'], payload, after_response=after_response)
    else:
        result = http.execute_http_replay(run['id'], payload)
        assert result['replay']['reproduced'] is True
        assert result['verification']['status'] == expected
        assert result['replay']['business_boundary']['reason'] == reason
    assert len(calls) == 10
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == int(expected == 'verified')
        assert db.execute("SELECT count(*) FROM verification_attempts WHERE status='machine_receipt'").fetchone()[0] == int(expected == 'verified')
