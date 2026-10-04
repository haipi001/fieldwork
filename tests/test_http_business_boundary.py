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
    ('policy_changed', 'rejected', None),
    ('oracle_failed', 'rejected', None),
    ('oracle_interrupted', 'rejected', None),
    ('oracle_cancelled', 'rejected', None),
    ('auth_disallowed', 'rejected', None),
    ('evidence_changed', 'rejected', None),
    ('archived', 'rejected', None),
])
def test_live_finalization_requires_business_boundary(client, object_server, monkeypatch, tmp_path, rule_case, expected, reason):
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
        'scope': {'allow_private_ips': True, 'allow_authentication': rule_case != 'auth_disallowed', 'http_object_read_rules': rules},
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
    assert preview['can_prove'] == (rule_case in ('owner_only', 'allowlist_denied', 'shared', 'changed', 'policy_changed', 'oracle_failed', 'evidence_changed', 'archived', 'oracle_interrupted', 'oracle_cancelled'))
    assert calls == []
    if rule_case in {"oracle_failed", "oracle_interrupted"}:
        import v5_verification
        original_verifier = v5_verification._run_local_verifier
        def fail_confirmation(_, **kwargs):
            raise RuntimeError("isolated oracle unavailable")
        monkeypatch.setattr(v5_verification, "_run_local_verifier", fail_confirmation)
    if rule_case == 'oracle_cancelled':
        import v5_verification
        script = tmp_path / 'waiting-verifier.py'
        script.write_text('import sys, time\nsys.stdin.buffer.read()\ntime.sleep(30)\n')
        monkeypatch.setattr(v5_verification, 'CHILD_SCRIPT', script)
        original_verifier = v5_verification._run_local_verifier
        def cancel_during_verifier(execution_input, **kwargs):
            with final_core.connect() as db:
                job_id = db.execute('SELECT id FROM verification_jobs WHERE candidate_id=?', (candidate['id'],)).fetchone()['id']
            responses = []
            timer = threading.Timer(.35, lambda: responses.append(client.post(f'/api/v1/verification-jobs/{job_id}/cancel').status_code))
            timer.start()
            try:
                return original_verifier(execution_input, **kwargs)
            finally:
                timer.cancel()
                timer.join()
                assert responses == [200]
        monkeypatch.setattr(v5_verification, '_run_local_verifier', cancel_during_verifier)
    def after_response():
        if rule_case in {'evidence_changed', 'archived'} and len(calls) == 1:
            with final_core.connect() as db:
                if rule_case == 'evidence_changed':
                    db.execute("UPDATE observations SET summary='Evidence revised during replay' WHERE id=?", (observation['id'],))
                else:
                    db.execute("UPDATE engagements_v2 SET status='archived' WHERE id=?", (engagement['id'],))
        if rule_case == "policy_changed" and len(calls) == 1:
            with final_core.connect() as db:
                db.execute("UPDATE execution_policies SET policy=? WHERE id=?",
                           (final_core.dump({**engagement["policy"], "max_requests_per_second": 1}),
                            engagement["current_policy_id"]))
        if rule_case == 'changed' and len(calls) == 10:
            with final_core.connect() as db:
                scope = {**engagement['scope'], 'http_object_read_rules': []}
                db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',
                           (final_core.dump(scope), engagement['current_scope_snapshot_id']))
    if expected == 'rejected':
        from fastapi import HTTPException
        error_type = http.VerificationCancelled if rule_case == "oracle_cancelled" else HTTPException
        message = '取消' if rule_case == 'oracle_cancelled' else '独立判定未完成' if rule_case in {'oracle_failed', 'oracle_interrupted'} else '执行策略已变化' if rule_case == 'policy_changed' else '未显式允许身份验证' if rule_case == 'auth_disallowed' else '证据材料已变化' if rule_case == 'evidence_changed' else '状态不允许复验' if rule_case == 'archived' else '业务权限规则已变化'
        with pytest.raises(error_type, match=message):
            http.execute_http_replay(run['id'], payload, after_response=after_response)
    else:
        result = http.execute_http_replay(run['id'], payload)
        assert result['replay']['reproduced'] is True
        assert result['verification']['status'] == expected
        assert result['replay']['business_boundary']['reason'] == reason
        if rule_case == 'owner_only':
            from v5_verification import get_receipt
            receipt = get_receipt(result['independent_verification']['receipt_id'])
            assert receipt['result']['classification'] == 'positive'
            assert receipt['integrity']['promotion_eligible']
            finding = final_core.get_finding(result['verification']['id'])
            assert finding['severity'] == 'unknown'
            assert 'source implementation has not been examined' in finding['verification']['root_cause']
            assert result['replay']['review_notes']['severity'] == 'high'
            assert result['replay']['review_notes']['machine_verified'] is False
            with final_core.connect() as db:
                machine = db.execute("SELECT result FROM verification_attempts WHERE status='machine_receipt'").fetchone()
                assert final_core.load(machine['result'])['v5_verification_receipt_id'] == receipt['id']
                task = db.execute('SELECT status FROM agent_tasks WHERE id=?', (result['independent_verification']['task_id'],)).fetchone()
                assert task['status'] == 'succeeded'
    assert len(calls) == (0 if rule_case == 'auth_disallowed' else 1 if rule_case in {'policy_changed', 'evidence_changed', 'archived'} else 10)
    if rule_case in {'oracle_failed', 'oracle_interrupted'}:
        from v5_verification import local_verifier_tick, get_receipt
        with final_core.connect() as db:
            job = db.execute('SELECT * FROM verification_jobs WHERE candidate_id=?', (candidate['id'],)).fetchone()
            pending = final_core.load(job['result'])['independent_verification']
            assert job['status'] == 'failed'
            checkpoint = final_core.load(job['result'])['replay_checkpoint']
            assert checkpoint['completed_responses'] == 10 and checkpoint['final_artifact_id']
        if rule_case == 'oracle_interrupted':
            with final_core.connect() as db:
                db.execute("UPDATE verification_jobs SET status='running' WHERE id=?", (job['id'],))
            final_core.init_final_db()
            with final_core.connect() as db:
                recovered = db.execute('SELECT * FROM verification_jobs WHERE id=?', (job['id'],)).fetchone()
                assert recovered['status'] == 'interrupted' and recovered['result'] == job['result']
        monkeypatch.setattr(v5_verification, '_run_local_verifier', original_verifier)
        retried = local_verifier_tick(1, pending['request_id'])
        receipt = get_receipt(retried['completed'][0]['receipt_id'])
        assert receipt['integrity']['promotion_eligible'] and len(calls) == 10

    if rule_case == 'oracle_cancelled':
        with final_core.connect() as db:
            job = db.execute('SELECT * FROM verification_jobs WHERE candidate_id=?', (candidate['id'],)).fetchone()
            assert job['status'] == 'cancelled'
            task = db.execute('SELECT status FROM agent_tasks').fetchone()
            assert task['status'] == 'cancelled'
            assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0] == 0
            assert final_core.load(job['result'])['replay_checkpoint']['completed_responses'] == 10
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == int(expected == 'verified')
        assert db.execute("SELECT count(*) FROM verification_attempts WHERE status='machine_receipt'").fetchone()[0] == int(expected == 'verified')
