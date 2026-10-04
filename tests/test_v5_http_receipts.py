"""Receipt integration over real local HTTP; discovery and credentials are fixtures."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import final_core as core
from tests.test_final import client
from tests.test_guided_research import wait
from tests.test_v5_http_workflow import prepared_live_candidate, workflow_path


@pytest.fixture
def controlled_server():
    state = {'fixed': False}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            role = self.headers.get('X-Role')
            calls.append((self.path, role))
            allowed = bool(role) and not (state['fixed'] and role == 'other' and self.path == '/object')
            value = ({'id': 'A' if role == 'owner' else 'B'} if self.path == '/me'
                     else {'owner_id': 'A', 'record': 'fixture record', 'token': 'fixture-private-value'}) if allowed else {'error': 'denied'}
            body = json.dumps(value).encode()
            self.send_response(200 if allowed else 403)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield (f'http://127.0.0.1:{server.server_port}', calls), state
    finally:
        server.shutdown()
        server.server_close()


def execute(client, monkeypatch, controlled_server, fixed=False):
    fixture, state = controlled_server
    run, candidate, project, calls = prepared_live_candidate(client, monkeypatch, fixture)
    state['fixed'] = fixed
    path = workflow_path(run, candidate)
    plan = client.get(path + '/execution-plan').json()
    assert plan['can_execute']
    response = client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True})
    assert response.status_code == 202
    job = wait(client, response.json())
    proof = job['result']['items'][0]['auto_verification']['independent_verification']
    assert proof.get('receipt_id'), proof
    receipt = client.get('/api/v1/verification/receipts/' + proof['receipt_id']).json()
    assert len(calls) == 13
    assert receipt['integrity']['promotion_eligible'] is True
    assert 'fixture-private-value' not in json.dumps(receipt)
    return run, candidate, project, job, proof, receipt


@pytest.mark.parametrize('clean_run', range(3))
@pytest.mark.parametrize('fixed,status,classification', [(False, 'verified', 'positive'), (True, 'refuted', 'repaired_negative')])
def test_three_clean_real_http_runs_and_negative_controls(client, monkeypatch, controlled_server, clean_run, fixed, status, classification):
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server, fixed)
    assert receipt['result']['status'] == status
    assert receipt['result']['classification'] == classification
    assert receipt['environment']['process_isolation']['network_denied'] is True
    assert receipt['environment']['process_isolation']['child_pid'] != receipt['environment']['process_isolation']['parent_pid']
    with core.connect() as db:
        node = db.execute('SELECT * FROM research_nodes WHERE id=?', (proof['canonical_result_id'],)).fetchone()
        assert node['node_type'] == 'canonical_result' and node['source_ref'] == proof['claim_id']
        assert node['status'] == status
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM research_nodes WHERE node_type=?', ('counterevidence',)).fetchone()[0] == int(fixed)


@pytest.mark.parametrize('change', ['observation', 'identity', 'scope', 'policy', 'candidate', 'artifact', 'cancelled_job'])
def test_receipt_stales_and_promotion_rejects_current_input_changes(client, monkeypatch, controlled_server, change):
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server)
    with core.connect() as db:
        if change == 'observation':
            db.execute("UPDATE observations SET summary='Changed evidence' WHERE run_id=?", (run,))
        elif change == 'identity':
            db.execute("UPDATE identity_profiles SET session_status='expired'")
        elif change == 'scope':
            db.execute("UPDATE scope_snapshots SET rules=rules || ' ' WHERE id=?", (project['current_scope_snapshot_id'],))
        elif change == 'policy':
            db.execute("UPDATE execution_policies SET policy=policy || ' ' WHERE id=?", (project['current_policy_id'],))
        elif change == 'candidate':
            db.execute("UPDATE candidate_findings SET hypothesis='Changed claim' WHERE id=?", (candidate['id'],))
        elif change == 'cancelled_job':
            db.execute("UPDATE guided_research_jobs SET status='cancelled',cancel_requested=1 WHERE id=?", (job['id'],))
        else:
            artifact = db.execute("SELECT uri FROM artifacts WHERE run_id=? AND kind='http.replay'", (run,)).fetchone()
            Path(artifact['uri']).write_text('{}')
    current = client.get('/api/v1/verification/receipts/' + proof['receipt_id']).json()
    assert not current['integrity']['current_inputs_match']
    assert not current['integrity']['promotion_eligible']
    latest = client.get('/api/v1/guided-research/' + job['id']).json()
    assert latest['result']['items'][0]['auto_verification']['independent_verification']['current_inputs_match'] is False
    promotion = client.post('/api/v1/research/nodes', json={'campaign_id': proof['campaign_id'], 'node_type': 'canonical_result',
        'title': 'Must reject stale receipt', 'source_ref': proof['claim_id'],
        'attributes': {'verification_receipt_id': proof['receipt_id'], 'verification_outcome': 'verified'}})
    assert promotion.status_code == 409


def test_changed_evidence_between_oracle_and_issue_cannot_mint_receipt(client, monkeypatch, controlled_server):
    import v5_verification as verifier
    original = verifier._run_local_verifier
    def changed(execution_input):
        result, proof = original(execution_input)
        with core.connect() as db:
            db.execute("UPDATE observations SET summary='Changed after independent oracle'")
        return result, proof
    monkeypatch.setattr(verifier, '_run_local_verifier', changed)
    fixture, state = controlled_server
    run, candidate, project, calls = prepared_live_candidate(client, monkeypatch, fixture)
    path = workflow_path(run, candidate)
    plan = client.get(path + '/execution-plan').json()
    job = wait(client, client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True}).json())
    independent = job['result']['items'][0]['auto_verification']['independent_verification']
    assert independent['status'] == 'failed_attempt' and 'receipt_id' not in independent
    assert len(calls) == 13
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM research_nodes WHERE node_type='canonical_result'").fetchone()[0] == 0
