import copy
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import app
import agent_audit as audit
import final_core as core
import reporting
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DB', tmp_path / 'test.db')
    monkeypatch.setattr(core, 'DB', tmp_path / 'test.db')
    monkeypatch.setattr(core, 'LOCAL_DATA_ROOT', tmp_path)
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path / 'exports')
    with TestClient(app.app, base_url='http://127.0.0.1:8000') as client:
        yield client


@pytest.fixture
def fixture():
    return audit.demo_fixture()


def create(client, fixture, policy=True):
    body = copy.deepcopy(fixture['audit'])
    if not policy:
        body['policy'] = None
    response = client.post('/api/v1/agent-audit/audits', json=body)
    assert response.status_code == 201, response.text
    return response.json()['id']


def upload(client, aid, body):
    response = client.post(f'/api/v1/agent-audit/audits/{aid}/imports', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def analyze(client, aid):
    response = client.post(f'/api/v1/agent-audit/audits/{aid}/analyze')
    assert response.status_code == 200, response.text
    return response.json()


def verify(client, aid, cid):
    return client.post(f'/api/v1/agent-audit/audits/{aid}/incidents/{cid}/verify')


def register_collector(client):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    response = client.post('/api/v1/agent-audit/collectors', json={
        'name': 'Local OS Collector', 'key_id': 'local-os-1', 'public_key': base64.b64encode(public).decode()})
    assert response.status_code == 201, response.text
    assert 'public_key' not in response.json()
    return private, response.json()


def signed_body(private, collector, aid, body, sequence=1, nonce='0123456789abcdef'):
    value = copy.deepcopy(body)
    value['independent_attested'] = False
    value.update(collector_id=collector['id'], sequence=sequence, nonce=nonce, signed_at='2026-09-14T14:03:00+08:00')
    payload = audit.signing_payload(aid, audit.ImportInput.model_validate({**value, 'signature': 'pending'}), hashlib.sha256(value['content'].encode()).hexdigest())
    value['signature'] = base64.b64encode(private.sign(audit.canonical_bytes(payload))).decode()
    return value


def test_demo_real_pipeline_and_capsule(client):
    response = client.post('/api/v1/agent-audit/demo')
    assert response.status_code == 201, response.text
    result = response.json()
    assert result['demo'] and len(result['events']) == 3
    assert not result['findings']
    assert [r['status'] for r in result['analysis']['reconciliation']['rows']] == ['ALIGNED','ALIGNED','CONTRADICTED']
    assert len(result['candidates']) == 1
    assert {event['policy_decision'] for event in result['events']} == {'allowed', 'violation'}
    cid = result['candidates'][0]['id']
    assert result['candidates'][0]['category'] == 'NETWORK_BOUNDARY'
    response = verify(client, result['id'], cid)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'verified'
    proof = response.json()['verification']
    assert proof['counterevidence']['checked'] and proof['synthetic']
    assert proof['self_report_status'] == 'contradicted'
    response = client.get(f"/api/v1/agent-audit/audits/{result['id']}/capsule")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert {'manifest.json','policy_snapshot.json','normalized_events.jsonl','self_report.json','reconciliation.json','incident_findings.json','evidence_manifest.json','hashes.sha256','report.md'} <= set(z.namelist())
        manifest = json.loads(z.read('package_manifest.json'))
        for name, sha in manifest['checksums'].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == sha
        for line in z.read('hashes.sha256').decode().splitlines():
            sha, name = line.split('  ', 1)
            assert hashlib.sha256(z.read(name)).hexdigest() == sha
        assert b'SIMULATED' in z.read('report.md')
    shared = client.get(f"/api/v1/findings?mode=agent_audit&run_id={result['run_id']}").json()
    assert len(shared['verified']) == 1
    assert not client.get('/api/v1/findings?mode=traditional').json()['verified']
    assert not client.get('/api/v1/findings?mode=web3').json()['verified']


def test_normal_aligned_no_incident(client, fixture):
    aid = create(client, fixture)
    body = copy.deepcopy(fixture['imports'][0])
    body['content'] = json.dumps({'events': json.loads(body['content'])['events'][:2]})
    upload(client, aid, body)
    body = copy.deepcopy(fixture['imports'][1])
    body['content'] = json.dumps({'claims': json.loads(body['content'])['claims'][:2]})
    upload(client, aid, body)
    result = analyze(client, aid)
    assert not result['candidates']
    assert all(r['status']=='ALIGNED' for r in result['analysis']['reconciliation']['rows'])


@pytest.mark.parametrize('kind', ['omitted','unsupported','unknown'])
def test_reconciliation_states(client, fixture, kind):
    aid = create(client, fixture)
    upload(client, aid, fixture['imports'][0])
    body = copy.deepcopy(fixture['imports'][1])
    claims = json.loads(body['content'])['claims'][:2]
    if kind == 'unsupported':
        claims.append({'statement':'读取了别的文件','action_type':'read','resource':'/workspace/missing','assertion':'occurred'})
    if kind == 'unknown':
        claims.append({'statement':'也许发生过不确定行为'})
    body['content'] = json.dumps({'claims':claims})
    upload(client, aid, body)
    result = analyze(client, aid)
    assert kind.upper() in [r['status'] for r in result['analysis']['reconciliation']['rows']]


@pytest.mark.parametrize('block', ['policy','self_report','llm','actor','session','time','status','unattested'])
def test_verification_gate_blocks_missing_basis(client, fixture, block):
    aid = create(client, fixture, policy=block != 'policy')
    body = copy.deepcopy(fixture['imports'][0])
    events = json.loads(body['content'])['events']
    event = events[2]
    if block in {'self_report','llm'}:
        event['source_type'] = 'self_report' if block=='self_report' else 'agent_trace'
        event['independent'] = True # must not be trusted
    if block == 'actor': event['actor'] = 'other-agent'
    if block == 'session': event['session_id'] = 'other-session'
    if block == 'time': event['timestamp'] = '2020-01-01T00:00:00Z'
    if block == 'status': event['status'] = 'blocked'
    if block == 'unattested': body['provenance'] = 'agent_supplied'
    body['content'] = json.dumps({'events':events})
    upload(client, aid, body)
    upload(client, aid, fixture['imports'][1])
    result = analyze(client, aid)
    if result['candidates']:
        response = verify(client, aid, result['candidates'][0]['id'])
        assert response.status_code == 409, response.text
    assert not client.get(f'/api/v1/agent-audit/audits/{aid}').json()['findings']


def test_policy_no_snapshot_contradiction_cannot_verify(client, fixture):
    aid = create(client, fixture, policy=False)
    for body in fixture['imports']: upload(client, aid, body)
    result = analyze(client, aid)
    assert result['candidates'][0]['category']=='SELF_REPORT_CONTRADICTED'
    assert verify(client, aid, result['candidates'][0]['id']).status_code == 409


@pytest.mark.parametrize('path,violation', [('/workspace/file',False),('/workspace-other/file',True),('/workspace/../secret',True),('/etc/passwd',True)])
def test_filesystem_boundary(client, fixture, path, violation):
    aid = create(client, fixture)
    body = copy.deepcopy(fixture['imports'][0])
    event = json.loads(body['content'])['events'][0]
    event.update(filesystem_path=path,resource=path)
    body['content'] = json.dumps([event])
    upload(client, aid, body)
    result = analyze(client, aid)
    assert bool(result['candidates']) is violation
    if violation: assert result['candidates'][0]['category']=='FILESYSTEM_BOUNDARY'


def test_counterevidence_conflict_blocks(client, fixture):
    aid = create(client, fixture)
    body = copy.deepcopy(fixture['imports'][0])
    events = json.loads(body['content'])['events']
    events.append({**events[2], 'status':'blocked'})
    body['content'] = json.dumps({'events':events})
    upload(client, aid, body)
    result = analyze(client, aid)
    successful = next(e['id'] for e in result['events'] if e['source_type']=='network' and e['status']=='success')
    candidate = next(c for c in result['candidates'] if c['event_id']==successful)
    response = verify(client, aid, candidate['id'])
    assert response.status_code == 200, response.text
    assert response.json()['status']=='human_review'
    assert not client.get(f'/api/v1/agent-audit/audits/{aid}').json()['findings']


@pytest.mark.parametrize('tamper', ['file','policy','event','manifest'])
def test_tampering_blocks_verification_and_export(client, tamper):
    result = client.post('/api/v1/agent-audit/demo').json()
    with core.connect() as db:
        if tamper=='file':
            row=db.execute("SELECT * FROM artifacts WHERE run_id=? AND kind='agent_event'",(result['run_id'],)).fetchone()
            Path(row['uri']).write_text('{}')
        elif tamper=='policy':
            db.execute("UPDATE execution_policies SET policy='{}' WHERE engagement_id=?",(result['id'],))
        elif tamper=='event':
            db.execute("UPDATE agent_events SET event_json='{}' WHERE audit_id=?",(result['id'],))
        else:
            row=db.execute('SELECT input_manifest FROM agent_audits WHERE id=?',(result['id'],)).fetchone()
            value=json.loads(row[0]);value['imports'][0]['self_report_complete']=True
            db.execute('UPDATE agent_audits SET input_manifest=? WHERE id=?',(json.dumps(value),result['id']))
    assert verify(client,result['id'],result['candidates'][0]['id']).status_code==409
    assert client.get(f"/api/v1/agent-audit/audits/{result['id']}/capsule").status_code==409


def test_frozen_import_idempotency_and_cross_audit(client, fixture):
    aid=create(client,fixture)
    upload(client,aid,fixture['imports'][0])
    assert client.post(f'/api/v1/agent-audit/audits/{aid}/imports',json=fixture['imports'][0]).status_code==409
    result=analyze(client,aid)
    assert len(analyze(client,aid)['candidates'])==1
    assert client.post(f'/api/v1/agent-audit/audits/{aid}/imports',json=fixture['imports'][1]).status_code==409
    other=create(client,fixture)
    assert verify(client,other,result['candidates'][0]['id']).status_code==409
    assert client.post(f'/api/v1/engagements/{aid}/start').status_code in {409,422}


def test_redaction_and_escaped_report(client, fixture):
    fixture['audit']['name']='<script>alert(1)</script>'
    aid=create(client,fixture)
    body=copy.deepcopy(fixture['imports'][0])
    events=json.loads(body['content'])['events']
    events[0]['headers']={'Authorization':'Bearer SECRET_SENTINEL','Cookie':'sid=SECRET_SENTINEL'}
    events[0]['resource']='https://example.test/?token=SECRET_SENTINEL'
    body['content']=json.dumps(events)
    upload(client,aid,body)
    result=analyze(client,aid)
    response=client.get(f'/api/v1/agent-audit/audits/{aid}/capsule')
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert all(b'SECRET_SENTINEL' not in z.read(name) for name in z.namelist())
    html=client.get(f'/api/v1/agent-audit/audits/{aid}/report?format=html').text
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert result['metrics']['self_audit_recall'] is None


def test_jsonl_and_untrusted_provenance(client, fixture):
    aid=create(client,fixture)
    body=copy.deepcopy(fixture['imports'][0])
    body['kind']='jsonl';body['content']='\n'.join(json.dumps(e) for e in json.loads(body['content'])['events'])
    upload(client,aid,body)
    assert len(analyze(client,aid)['events'])==3
    aid=create(client,fixture);body['independent_attested']=False
    assert client.post(f'/api/v1/agent-audit/audits/{aid}/imports',json=body).status_code==422
    body.update(kind='process',content='rm -rf /',provenance='agent_supplied')
    assert client.post(f'/api/v1/agent-audit/audits/{aid}/imports',json=body).status_code==422


def test_signed_collector_import_verifies_authenticity_and_capsule(client, fixture):
    aid = create(client, fixture)
    private, collector = register_collector(client)
    body = signed_body(private, collector, aid, fixture['imports'][0])
    result = upload(client, aid, body)
    assert result['events'][0]['trust_level'] == 'signed_collector'
    assert result['events'][0]['authenticity_verified'] is True
    assert result['collector_attestations'][0]['fingerprint'] == collector['fingerprint']
    upload(client, aid, fixture['imports'][1])
    result = analyze(client, aid)
    response = verify(client, aid, result['candidates'][0]['id'])
    assert response.status_code == 200, response.text
    assert response.json()['verification']['authenticity_verified'] is True
    assert response.json()['verification']['verification_basis'] == 'signed_collector_reconstruction'
    capsule = client.get(f'/api/v1/agent-audit/audits/{aid}/capsule')
    with zipfile.ZipFile(io.BytesIO(capsule.content)) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['authenticity_verified'] is True
        assert json.loads(z.read('collector_attestations.json'))[0]['key_id'] == 'local-os-1'


def test_signed_collector_rejects_tampering_replay_rollback_and_revocation(client, fixture):
    private, collector = register_collector(client)
    aid = create(client, fixture)
    valid = signed_body(private, collector, aid, fixture['imports'][0])
    tampered = copy.deepcopy(valid)
    tampered['content'] += ' '
    assert client.post(f'/api/v1/agent-audit/audits/{aid}/imports', json=tampered).status_code == 422
    upload(client, aid, valid)
    other = create(client, fixture)
    replay = signed_body(private, collector, other, fixture['imports'][0], sequence=2, nonce=valid['nonce'])
    assert client.post(f'/api/v1/agent-audit/audits/{other}/imports', json=replay).status_code == 409
    rollback = signed_body(private, collector, other, fixture['imports'][0], sequence=1, nonce='fedcba9876543210')
    assert client.post(f'/api/v1/agent-audit/audits/{other}/imports', json=rollback).status_code == 409
    assert client.post(f"/api/v1/agent-audit/collectors/{collector['id']}/revoke").status_code == 200
    revoked = signed_body(private, collector, other, fixture['imports'][0], sequence=2, nonce='abcdef0123456789')
    assert client.post(f'/api/v1/agent-audit/audits/{other}/imports', json=revoked).status_code == 409
    assert client.get('/api/v1/agent-audit/collectors').json()[0]['status'] == 'revoked'
