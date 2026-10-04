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
    state = {'fixed': False, 'owner_id':'A', 'other_id':'B'}
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            role = self.headers.get('X-Role')
            calls.append((self.path, role))
            allowed = bool(role) and not (state['fixed'] and role == 'other' and self.path == '/object')
            value = ({'id': state['owner_id'] if role == 'owner' else state['other_id']} if self.path == '/me'
                     else {'owner_id': state['owner_id'], 'record': 'fixture record', 'token': 'fixture-private-value'}) if allowed else {'error': 'denied'}
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
    if fixed:
        assert client.get('/api/v1/verification/receipts/' + proof['receipt_id'] + '/http-finding-plan').status_code == 409
    assert receipt['environment']['process_isolation']['network_denied'] is True
    assert receipt['environment']['process_isolation']['child_pid'] != receipt['environment']['process_isolation']['parent_pid']
    with core.connect() as db:
        node = db.execute('SELECT * FROM research_nodes WHERE id=?', (proof['canonical_result_id'],)).fetchone()
        assert node['node_type'] == 'canonical_result' and node['source_ref'] == proof['claim_id']
        assert node['status'] == status
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM research_nodes WHERE node_type=?', ('counterevidence',)).fetchone()[0] == int(fixed)


@pytest.mark.parametrize('change', ['observation', 'identity', 'scope', 'policy', 'candidate', 'artifact', 'cancelled_job', 'evidence_added', 'evidence_removed', 'evidence_duplicate', 'counteredge'])
def test_receipt_stales_and_promotion_rejects_current_input_changes(client, monkeypatch, controlled_server, change):
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server)
    with core.connect() as db:
        if change in {'evidence_added', 'evidence_removed', 'evidence_duplicate'}:
            row = db.execute('SELECT evidence_ids FROM candidate_findings WHERE id=?', (candidate['id'],)).fetchone()
            identifiers = core.load(row['evidence_ids'])
            if change == 'evidence_added':
                new_id = core.uid('evidence')
                db.execute('INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)',
                           (new_id, None, run, 'counterevidence', 'New contradictory material requires review', None, 'counter', core.utcnow()))
                identifiers.append(new_id)
            elif change == 'evidence_removed':
                identifiers.pop()
            else:
                identifiers.append(identifiers[0])
            db.execute('UPDATE candidate_findings SET evidence_ids=? WHERE id=?', (core.dump(identifiers), candidate['id']))
            if change == 'evidence_added':
                from v5_http_receipts import capture_sources
                artifact = db.execute("SELECT uri FROM artifacts WHERE run_id=? AND kind='http.replay'", (run,)).fetchone()
                binding = json.loads(Path(artifact['uri']).read_text())['source_snapshot']['binding']
                refreshed = capture_sources(db, candidate['id'], binding)
                assert any(ref['table'] == 'evidence_v2' and ref['id'] == new_id for ref in refreshed['refs'])
        elif change == 'observation':
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
        elif change == 'artifact':
            artifact = db.execute("SELECT uri FROM artifacts WHERE run_id=? AND kind='http.replay'", (run,)).fetchone()
            Path(artifact['uri']).write_text('{}')
    if change == 'counteredge':
        counter = client.post('/api/v1/research/nodes', json={'campaign_id': proof['campaign_id'],
            'node_type': 'counterevidence', 'title': 'New contradiction', 'body': 'New evidence requires reviewing the selected claim'}).json()
        edge = client.post('/api/v1/research/edges', json={'campaign_id': proof['campaign_id'],
            'source_id': counter['id'], 'target_id': proof['claim_id'], 'relation_type': 'contradicts'})
        assert edge.status_code == 201
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
    def changed(execution_input, **kwargs):
        result, proof = original(execution_input, **kwargs)
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


@pytest.mark.parametrize('change', ['observation', 'evidence_added', 'counteredge'])
def test_reviewed_v5_result_promotes_idempotently_and_exports_bound_proof(client, monkeypatch, controlled_server, change):
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server)
    base = '/api/v1/verification/receipts/' + proof['receipt_id']
    plan = client.get(base + '/http-finding-plan')
    assert plan.status_code == 200, plan.text
    material = plan.json()
    assert material['sends_requests'] is False and material['severity'] == 'unknown'
    assert client.post(base + '/promote-http', json={'source_fingerprint': material['source_fingerprint']}).status_code == 422
    assert client.post(base + '/promote-http', json={'authorized': True, 'source_fingerprint': '0'*64}).status_code == 409
    response = client.post(base + '/promote-http', json={'authorized': True, 'source_fingerprint': material['source_fingerprint']})
    assert response.status_code == 200, response.text
    identifier = response.json()['id']
    again = client.post(base + '/promote-http', json={'authorized': True, 'source_fingerprint': material['source_fingerprint']})
    assert again.status_code == 200 and again.json()['id'] == identifier
    finding = client.get('/api/v1/findings/' + identifier).json()
    assert finding['severity'] == 'unknown' and finding['impact']['demonstrated'] is True
    preview = client.post('/api/v1/findings/' + identifier + '/reports/hackerone/preview')
    assert preview.status_code == 200, preview.text
    assert 'Broader data access and severity are unproven' in preview.json()['content']
    with core.connect() as db:
        stored = db.execute('SELECT result FROM verification_attempts WHERE id=?', (finding['verification']['receipt_id'],)).fetchone()
        assert core.load(stored['result'], {})['v5_verification_receipt_id'] == proof['receipt_id']
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 1
    attachments = core.build_proof_attachments(*core.finding_report_inputs(identifier))
    assert 'v5-verification.json' in str(attachments)
    assert len(controlled_server[0][1]) == 13
    with core.connect() as db:
        if change == 'observation':
            db.execute("UPDATE observations SET summary='Changed material' WHERE run_id=?", (run,))
        elif change == 'evidence_added':
            row = db.execute('SELECT evidence_ids FROM candidate_findings WHERE id=?', (candidate['id'],)).fetchone()
            new_id = core.uid('evidence')
            db.execute('INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)',
                       (new_id, None, run, 'counterevidence', 'New counterevidence after formal promotion', None, 'counter', core.utcnow()))
            db.execute('UPDATE candidate_findings SET evidence_ids=? WHERE id=?',
                       (core.dump(core.load(row['evidence_ids']) + [new_id]), candidate['id']))
    if change == 'counteredge':
        counter = client.post('/api/v1/research/nodes', json={'campaign_id': proof['campaign_id'],
            'node_type': 'counterevidence', 'title': 'New report contradiction', 'body': 'Review new evidence before export'}).json()
        assert client.post('/api/v1/research/edges', json={'campaign_id': proof['campaign_id'],
            'source_id': counter['id'], 'target_id': proof['claim_id'], 'relation_type': 'contradicts'}).status_code == 201
    assert client.get(base + '/http-finding-plan').status_code == 409
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        core.build_proof_attachments(*core.finding_report_inputs(identifier))


@pytest.mark.parametrize('changed_principal', [None,'owner_id','other_id'])
def test_v5_fixed_receipt_closes_only_bound_retest_and_is_idempotent(client, monkeypatch, controlled_server, changed_principal):
    import traditional_runtime as http
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server)
    base='/api/v1/verification/receipts/' + proof['receipt_id']
    material=client.get(base + '/http-finding-plan').json()
    finding=client.post(base + '/promote-http',json={'authorized':True,'source_fingerprint':material['source_fingerprint']}).json()
    new_run=core.uid('run')
    with core.connect() as db:
        previous=dict(db.execute('SELECT * FROM analysis_runs WHERE id=?',(run,)).fetchone())
        previous['id']=new_run
        db.execute('INSERT INTO analysis_runs ('+','.join(previous)+') VALUES ('+','.join('?' for _ in previous)+')',list(previous.values()))
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(new_run,30,0,10,0,0,0,core.utcnow()))
        identities=db.execute('SELECT id,label FROM identities WHERE engagement_id=?',(project['id'],)).fetchall()
    retest=client.post('/api/v1/findings/'+finding['id']+'/retest-plans',json={'run_id':new_run,'note':'Actual two-round object authorization retest'})
    assert retest.status_code in (200,201),retest.text
    controlled_server[1]['fixed']=True
    if changed_principal:
        controlled_server[1][changed_principal]='C'
    by_role={row['label']:row['id'] for row in identities}
    origin=controlled_server[0][0]
    for path,role in [('/object','owner'),('/me','owner'),('/me','other')]:
        assert http.create_http_exchange(new_run,http.ExchangeRequestInput(url=origin+path,identity_id=by_role[role]))['response_status']==200
    path='/api/v1/runs/'+new_run+'/candidate-workflow/'+retest.json()['candidate_id']
    execution=client.get(path+'/execution-plan').json()
    assert execution['can_execute'],execution
    outcome=wait(client,client.post(path+'/execute',json={'authorized':True,'source_fingerprint':execution['source_fingerprint']}).json())
    negative=outcome['result']['items'][0]['auto_verification']['independent_verification']
    assert negative['status']=='refuted'
    base='/api/v1/verification/receipts/'+negative['receipt_id']
    plan=client.get(base+'/http-fixed-plan')
    if changed_principal:
        assert plan.status_code==409,plan.text
        with core.connect() as db:
            assert db.execute('SELECT status FROM finding_lifecycle WHERE finding_id=?',(finding['id'],)).fetchone()[0]=='retest_required'
            assert db.execute("SELECT count(*) FROM verification_attempts WHERE status='machine_negative_receipt'").fetchone()[0]==0
        return
    assert plan.status_code==200,plan.text
    assert plan.json()['finding_id']==finding['id'] and plan.json()['sends_requests'] is False
    assert client.post(base+'/confirm-http-fixed',json={'source_fingerprint':plan.json()['source_fingerprint']}).status_code==422
    fixed=client.post(base+'/confirm-http-fixed',json={'authorized':True,'source_fingerprint':plan.json()['source_fingerprint']})
    assert fixed.status_code==200,fixed.text
    again=client.post(base+'/confirm-http-fixed',json={'authorized':True,'source_fingerprint':plan.json()['source_fingerprint']})
    assert again.status_code==200,again.text
    assert again.json()['receipt_id']==fixed.json()['receipt_id']
    assert len(controlled_server[0][1])==26
    with core.connect() as db:
        assert db.execute('SELECT status FROM finding_lifecycle WHERE finding_id=?',(finding['id'],)).fetchone()[0]=='verified_fixed'
        row=db.execute("SELECT result FROM verification_attempts WHERE status='machine_negative_receipt'").fetchone()
        assert core.load(row['result'],{})['v5_verification_receipt_id']==negative['receipt_id']


@pytest.mark.parametrize('change', ['body', 'edge_attributes'])
def test_modified_receipt_derived_counterevidence_invalidates_negative_receipt(client, monkeypatch, controlled_server, change):
    run, candidate, project, job, proof, receipt = execute(client, monkeypatch, controlled_server, fixed=True)
    assert receipt['integrity']['promotion_eligible']
    with core.connect() as db:
        counter = db.execute("SELECT id FROM research_nodes WHERE source_type='http_counterevidence' AND source_ref=?", (proof['receipt_id'],)).fetchone()
        if change == 'body':
            db.execute("UPDATE research_nodes SET body='New contradictory material, not the derived summary' WHERE id=?", (counter['id'],))
        else:
            db.execute('UPDATE research_edges SET attributes_json=? WHERE source_id=? AND target_id=?',
                       (core.dump({'additional_counterevidence': True}), counter['id'], proof['claim_id']))
    current = client.get('/api/v1/verification/receipts/' + proof['receipt_id']).json()
    assert current['result']['classification'] == 'repaired_negative'
    assert not current['integrity']['current_inputs_match'] and not current['integrity']['promotion_eligible']
