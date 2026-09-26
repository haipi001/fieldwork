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
    monkeypatch.setattr(audit.application_ai_monitor, 'roots', lambda: {'Codex': tmp_path / 'codex-logs', 'Claude Code': tmp_path / 'claude-logs'})
    monkeypatch.setattr(audit.desktop_ai_monitor, 'snapshot', lambda: {
        'platform': 'Darwin', 'applications': [{'name': 'Cursor', 'installed': True}],
        'processes': {'42': {'pid': 42, 'parent_pid': 1, 'uid': 501, 'executable': 'Cursor', 'app': 'Cursor'}},
        'connections': {}, 'errors': [], 'coverage': {'processes': 'sampling', 'network': 'sampling', 'file_io': 'not_connected'}})
    with TestClient(app.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1',50000)) as client:
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
    assert len(result['findings']) == 1
    assert [r['status'] for r in result['analysis']['reconciliation']['rows']] == ['ALIGNED','ALIGNED','CONTRADICTED']
    assert len(result['candidates']) == 1
    assert {event['policy_decision'] for event in result['events']} == {'allowed', 'violation'}
    cid = result['candidates'][0]['id']
    assert result['candidates'][0]['category'] == 'NETWORK_BOUNDARY'
    response = verify(client, result['id'], cid)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'verified'
    proof = result['findings'][0]['verification']
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


def test_zero_config_monitor_records_desktop_events_and_stops_with_analysis(client):
    response = client.post('/api/v1/agent-audit/monitor/start')
    assert response.status_code == 201, response.text
    started = response.json()
    assert started['monitor']['status'] == 'active'
    assert started['identity']['name'] == '本机 AI 活动监控'
    assert started['policy'] is None
    assert started['monitor']['collector_kind'] == 'desktop'
    assert started['events'][0]['resource'] == '自动监控已启动'
    core.add_event('external-run', 'analysis', 'native_agent.page_observed', '只读浏览器已观察 https://example.test', {'turn': 1})
    response = client.post(f"/api/v1/agent-audit/monitor/{started['id']}/scan")
    assert response.status_code == 200, response.text
    scanned = response.json()
    assert any(event['actor'] == 'Cursor' and 'PID 42' in event['resource'] for event in scanned['events'])
    assert not any('example.test' in event['resource'] for event in scanned['events'])
    assert len(scanned['events']) == len(started['events'])
    response = client.post(f"/api/v1/agent-audit/monitor/{started['id']}/stop")
    assert response.status_code == 200, response.text
    stopped = response.json()
    assert stopped['monitor']['status'] == 'stopped'
    assert stopped['analysis'] is not None


def test_start_monitor_reuses_active_session(client):
    first = client.post('/api/v1/agent-audit/monitor/start').json()
    second = client.post('/api/v1/agent-audit/monitor/start').json()
    assert second['id'] == first['id']


def test_monitor_pause_resume_health_and_live_anomalies(client, monkeypatch):
    started = client.post('/api/v1/agent-audit/monitor/start').json()
    aid = started['id']
    assert started['monitor']['health']['status'] == 'healthy'
    paused = client.post(f'/api/v1/agent-audit/monitor/{aid}/pause').json()
    assert paused['monitor']['status'] == 'paused'
    snapshot = audit.desktop_ai_monitor.snapshot()
    snapshot['connections'] = {'42|socket': {'pid': 42, 'app': 'Cursor', 'host': 'example.test', 'port': 443}}
    monkeypatch.setattr(audit.desktop_ai_monitor, 'snapshot', lambda: snapshot)
    audit.scan_active_monitors()
    assert not any('example.test' in event['resource'] for event in client.get(f'/api/v1/agent-audit/audits/{aid}').json()['events'])
    resumed = client.post(f'/api/v1/agent-audit/monitor/{aid}/resume').json()
    assert resumed['monitor']['status'] == 'active'
    assert any('example.test' in event['resource'] for event in resumed['events'])
    assert resumed['monitor']['last_event_at']
    assert not resumed['live_anomalies'] # No arbitrary desktop permission policy.


def test_desktop_process_attribution_and_connection_parser():
    monitor = audit.desktop_ai_monitor
    processes = monitor.parse_processes('42 1 501 /Applications/Cursor.app/Contents/MacOS/Cursor\n43 42 501 /usr/bin/python3\n44 1 501 /tmp/cursor-token\n')
    assert set(processes) == {'42', '43'}
    assert processes['43']['app'] == 'Cursor'
    connections = monitor.parse_connections('p43\nf10\nn127.0.0.1:50000->[::1]:443\nTST=ESTABLISHED\n', processes)
    assert next(iter(connections.values()))['host'] == '::1'
    current = {'processes': processes, 'connections': connections, 'coverage': {'processes': 'sampling'}}
    events = monitor.events({}, current, core.utcnow(), 'session')
    assert len(events) == 3
    assert monitor.events(current, current, core.utcnow(), 'session') == []


def test_desktop_failure_does_not_generate_false_exit_events(client, monkeypatch):
    started = client.post('/api/v1/agent-audit/monitor/start').json()
    failed = {'processes': {}, 'connections': {}, 'applications': [], 'errors': ['采集超时'], 'coverage': {'processes': 'unavailable'}}
    monkeypatch.setattr(audit.desktop_ai_monitor, 'snapshot', lambda: failed)
    result = audit.scan_monitor(started['id'])
    assert len(result['events']) == len(started['events'])
    assert result['monitor']['last_error'] == '采集超时'
    assert '42' in result['monitor']['desktop']['processes']


def test_open_files_are_observations_not_read_write_claims():
    monitor = audit.desktop_ai_monitor
    processes = {'42': {'app': 'Cursor'}}
    output = 'p42\nfcwd\ntDIR\nn/work\nf7\nau\ntREG\nn/work/example.py\nf8\nar\ntIPv4\nnlocalhost:443\np99\nf4\naw\ntREG\nn/other/file\n'
    files = monitor.parse_open_files(output, processes)
    assert list(files) == ['42|7|/work/example.py']
    current = {'processes': {}, 'connections': {}, 'open_files': files, 'coverage': {'processes': 'sampling'}}
    observed = monitor.events({}, current, core.utcnow(), 'session')
    assert len(observed) == 1
    assert observed[0]['source_type'] == 'filesystem'
    assert observed[0]['action_type'] == 'unknown'
    assert observed[0]['status'] == 'observed'
    assert observed[0]['filesystem_path'] == '/work/example.py'
    assert monitor.events(current, current, core.utcnow(), 'session') == []


def test_process_reuse_and_missing_network_visibility():
    monitor = audit.desktop_ai_monitor
    first = monitor.parse_processes('42 1 501 Sat Sep 26 12:00:00 2026 /Applications/Cursor.app/Contents/MacOS/Cursor', with_start_time=True)
    reused = monitor.parse_processes('42 1 501 Sat Sep 26 12:01:00 2026 /Applications/Cursor.app/Contents/MacOS/Cursor', with_start_time=True)
    assert first['42']['started_at'] != reused['42']['started_at']
    sockets = 'p42\nf10\nn127.0.0.1:50000->example.test:443\nTST=ESTABLISHED\n'
    old = {'processes': first, 'connections': monitor.parse_connections(sockets, first), 'coverage': {'processes': 'sampling', 'network': 'sampling'}}
    new = {'processes': reused, 'connections': monitor.parse_connections(sockets, reused), 'coverage': old['coverage']}
    events = monitor.events(old, new, core.utcnow(), 'session')
    assert {e['command_category'] for e in events} == {'process_observed', 'connection_observed'}
    assert all(e['process_id'] == 42 for e in events)
    assert all(e['process_started_at'] == reused['42']['started_at'] for e in events)
    new['connections'] = {}
    new['coverage'] = {'processes': 'sampling', 'network': 'partial'}
    assert not any(e['command_category'] == 'connection_disappeared' for e in monitor.events(old, new, core.utcnow(), 'session'))
    new['coverage']['network'] = 'sampling'
    assert any(e['command_category'] == 'connection_disappeared' for e in monitor.events(old, new, core.utcnow(), 'session'))


def test_monitor_failure_pauses_visibly(client, monkeypatch):
    started = client.post('/api/v1/agent-audit/monitor/start').json()
    def fail():
        raise RuntimeError('internal details should not be displayed')
    monkeypatch.setattr(audit.desktop_ai_monitor, 'snapshot', fail)
    audit.scan_active_monitors()
    result = client.get(f"/api/v1/agent-audit/audits/{started['id']}").json()
    assert result['monitor']['status'] == 'paused'
    assert 'RuntimeError' in result['monitor']['last_error']
    assert 'internal details' not in result['monitor']['last_error']


def test_monitor_app_log_is_automatic_and_never_independent(client, tmp_path):
    root = tmp_path / 'codex-logs'
    root.mkdir()
    started = client.post('/api/v1/agent-audit/monitor/start').json()
    path = root / 'new-session.jsonl'
    path.write_text(json.dumps({'timestamp': core.utcnow(), 'type': 'response_item', 'payload': {
        'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'local-call',
        'input': 'sensitive private command'}}) + '\n')
    result = audit.scan_monitor(started['id'])
    tool = next(e for e in result['events'] if e['source_type'] == 'tool_call')
    assert tool['tool_name'] == 'exec' and tool['status'] == 'requested'
    assert tool['independent'] is False and tool['authenticity_verified'] is False
    assert tool['trust_level'] == 'agent_supplied'
    assert 'sensitive private command' not in json.dumps(result)
    assert 'application_log_state' not in result['monitor']['desktop']
    assert len(audit.scan_monitor(started['id'])['events']) == len(result['events'])
    client.post(f"/api/v1/agent-audit/monitor/{started['id']}/pause")
    with path.open('a') as stream:
        stream.write(json.dumps({'timestamp': core.utcnow(), 'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'local-call', 'output': 'private output'}}) + '\n')
    resumed = client.post(f"/api/v1/agent-audit/monitor/{started['id']}/resume").json()
    assert len(resumed['events']) == len(result['events'])
    stopped = client.post(f"/api/v1/agent-audit/monitor/{started['id']}/stop").json()
    assert not stopped['findings']


def browser_pair(client, aid):
    response = client.post(f'/api/v1/agent-audit/monitor/{aid}/browser/package')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        config = json.loads(archive.read('config.js').decode().split(' = ', 1)[1].rstrip(';'))
        manifest = json.loads(archive.read('manifest.json'))
        assert '<all_urls>' not in json.dumps(manifest)
        assert not manifest.get('content_scripts')
        assert 'webRequest' in manifest['optional_permissions']
    return config


def test_browser_pair_metadata_privacy_retry_and_revoke(client):
    aid = client.post('/api/v1/agent-audit/monitor/start').json()['id']
    config = browser_pair(client, aid)
    headers = {'Authorization': 'Bearer ' + config['token'], 'Origin': 'chrome-extension://' + 'a' * 32}
    batch = {'sequence': 1, 'events': [{'timestamp': core.utcnow(), 'host': 'chatgpt.com',
        'method': 'POST', 'kind': 'request', 'phase': 'completed', 'status_code': 200}]}
    endpoint = '/api/v1/agent-audit/browser/events'
    assert client.post(endpoint, json=batch).status_code == 401
    accepted = client.post(endpoint, json=batch, headers=headers)
    assert accepted.status_code == 200 and accepted.json()['accepted'] == 1
    assert client.post(endpoint, json=batch, headers=headers).json()['duplicate'] is True
    result = client.get(f'/api/v1/agent-audit/audits/{aid}').json()
    event = next(e for e in result['events'] if e['source_type'] == 'browser')
    assert event['actor'] == 'ChatGPT (Browser)' and not event['independent']
    assert not event['authenticity_verified'] and event['status'] == 'observed'
    assert result['monitor']['browser']['status'] == 'connected'
    assert config['token'] not in json.dumps(result)
    private = copy.deepcopy(batch)
    private['sequence'] = 2
    private['events'][0]['url'] = 'https://chatgpt.com/private?token=secret'
    assert client.post(endpoint, json=private, headers=headers).status_code == 422
    private['events'][0].pop('url')
    private['events'][0]['host'] = 'private.example'
    assert client.post(endpoint, json=private, headers=headers).status_code == 422
    assert client.post(endpoint, json=batch, headers={**headers, 'Origin': 'https://evil.example'}).status_code == 403
    revoked = client.post(f"/api/v1/agent-audit/monitor/{aid}/browser/{config['connectionId']}/revoke")
    assert revoked.status_code == 200
    assert client.post(endpoint, json=batch, headers=headers).status_code == 401


def test_browser_pause_stop_and_package_origin_boundaries(client):
    aid = client.post('/api/v1/agent-audit/monitor/start').json()['id']
    assert client.post(f'/api/v1/agent-audit/monitor/{aid}/browser/package', headers={'Origin':'https://evil.example'}).status_code == 403
    config = browser_pair(client, aid)
    headers = {'Authorization': 'Bearer ' + config['token']}
    client.post(f'/api/v1/agent-audit/monitor/{aid}/pause')
    paused_event = {'timestamp': core.utcnow(), 'host':'claude.ai', 'method':'POST', 'kind':'request', 'phase':'started'}
    assert client.post('/api/v1/agent-audit/browser/events', json={'sequence':1,'events':[]}, headers=headers).status_code == 409
    assert client.post(f'/api/v1/agent-audit/monitor/{aid}/browser/package').status_code == 409
    client.post(f'/api/v1/agent-audit/monitor/{aid}/resume')
    assert client.post('/api/v1/agent-audit/browser/events', json={'sequence':1,'events':[]}, headers=headers).status_code == 200
    old = client.post('/api/v1/agent-audit/browser/events', json={'sequence':2,'events':[paused_event]}, headers=headers)
    assert old.status_code == 200 and old.json()['dropped'] == 1 and old.json()['accepted'] == 0
    client.post(f'/api/v1/agent-audit/monitor/{aid}/stop')
    assert client.post('/api/v1/agent-audit/browser/events', json={'sequence':2,'events':[]}, headers=headers).status_code == 401


def test_monitor_auto_pauses_when_storage_is_critical(client, monkeypatch):
    started = client.post('/api/v1/agent-audit/monitor/start').json()
    monkeypatch.setattr(audit.shutil, 'disk_usage', lambda _: type('Usage', (), {'total': 1000, 'used': 995, 'free': 5})())
    result = audit.scan_monitor(started['id'])
    assert result['monitor']['status'] == 'paused'
    assert '存储空间不足' in result['monitor']['last_error']
    response = client.post(f"/api/v1/agent-audit/monitor/{started['id']}/resume")
    assert response.status_code == 507


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
    assert result['reconciliation_record']['version'] == 1
    assert result['reconciliation_record']['input_digest'] == result['analysis']['input_digest']


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
    assert response.json()['verification']['verification_basis'] == 'single_independent_source'
    capsule = client.get(f'/api/v1/agent-audit/audits/{aid}/capsule')
    with zipfile.ZipFile(io.BytesIO(capsule.content)) as z:
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['authenticity_verified'] is True
        assert json.loads(z.read('collector_attestations.json'))[0]['key_id'] == 'local-os-1'


def test_fieldwork_demo_trace_parser_and_one_click_verified_chain(client, fixture):
    aid = create(client, fixture)
    body = copy.deepcopy(fixture['imports'][0])
    body['kind'] = 'fieldwork_demo_trace'
    result = upload(client, aid, body)
    assert len(result['events']) == 3
    demo = client.post('/api/v1/agent-audit/demo').json()
    assert demo['analysis'] and len(demo['candidates']) == 1 and len(demo['findings']) == 1
    assert demo['candidates'][0]['status'] == 'verified'


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
