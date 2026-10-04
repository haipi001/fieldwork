"""Kill an owned supervisor process after a committed real HTTP checkpoint."""
import hashlib
import json
import selectors
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import final_core as core
from tests.test_final import client
from tests.test_http_business_boundary import object_server


SUPERVISOR = '''
import json, sys, time
from pathlib import Path
import final_core as core
import traditional_runtime as http
import v5_verification as verification
config = json.loads(sys.stdin.readline())
core.DB = Path(config['db'])
core.LOCAL_DATA_ROOT = core.DB.parent
http.ARTIFACT_ROOT = Path(config['artifacts'])
def committed(checkpoint):
    stop = (checkpoint['state'] == 'responses_complete' if config['complete']
            else checkpoint['completed_responses'] == 1)
    if stop and not config['leased']:
        print(json.dumps(checkpoint), flush=True)
        time.sleep(60)
def claimed(execution_input, **kwargs):
    # Short real lease expiry for the fixture; no fake clock or historical timestamp.
    from datetime import datetime, timedelta, timezone
    with core.connect() as db:
        job = db.execute('SELECT result FROM verification_jobs WHERE id=?', (config['job'],)).fetchone()
        result = core.load(job['result'])
        task_id = result['independent_verification']['task_id']
        db.execute('UPDATE agent_tasks SET lease_expires_at=? WHERE id=?',
                   ((datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(), task_id))
        task = dict(db.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone())
    print(json.dumps({**result['replay_checkpoint'], 'lease_owner': task['lease_owner'],
                      'task_id': task_id}), flush=True)
    time.sleep(60)
if config['leased']:
    verification._run_local_verifier = claimed
http.execute_http_replay(config['run'], http.HttpReplayInput(**config['body']),
                         job_id=config['job'], checkpoint_callback=committed)
'''

RECOVERY = '''
import json, sys, time
from pathlib import Path
import final_core as core
config = json.loads(sys.stdin.readline())
core.DB = Path(config['db'])
core.LOCAL_DATA_ROOT = core.DB.parent
core.init_final_db()
tick = recovery = receipt = None
if config['leased']:
    import traditional_runtime as http
    import v5_orchestration as orchestration
    import v5_verification as verification
    http.ARTIFACT_ROOT = Path(config['artifacts'])
    time.sleep(2.1)
    recovery = orchestration.recover_expired_leases()
    with core.connect() as db:
        job = db.execute('SELECT result FROM verification_jobs WHERE id=?', (config['job'],)).fetchone()
        request = core.load(job['result'])['independent_verification']
    tick = verification.local_verifier_tick(1, request['request_id'])
    if tick['completed'] and tick['completed'][0].get('receipt_id'):
        receipt = verification.get_receipt(tick['completed'][0]['receipt_id'])
with core.connect() as db:
    job = dict(db.execute('SELECT * FROM verification_jobs WHERE id=?', (config['job'],)).fetchone())
    run = db.execute('SELECT status FROM analysis_runs WHERE id=?', (config['run'],)).fetchone()
print(json.dumps({'job': job, 'run_status': run['status'], 'recovery': recovery,
                  'tick': tick, 'receipt': receipt}), flush=True)
'''


@pytest.mark.parametrize('complete,leased', [(False, False), (True, False), (True, True)],
                         ids=['partial', 'all-responses', 'claimed-verifier'])
def test_sigkill_preserves_committed_http_material_without_promoting(client, object_server, tmp_path, complete, leased):
    origin, calls = object_server
    target = origin + '/object'
    engagement = client.post('/api/v1/engagements', json={
        'name': 'Owned process recovery fixture', 'target': origin, 'mode': 'traditional',
        'scope': {'allow_private_ips': True, 'allow_authentication': True,
                  'http_object_read_rules': [{'target': target, 'access': 'owner_only',
                                              'source': 'Local fixture ownership specification'}]},
        'policy': {'max_requests_per_second': 1000},
    })
    assert engagement.status_code == 201
    confirmed = client.post('/api/v1/engagements/' + engagement.json()['id'] + '/confirm')
    assert confirmed.status_code == 200
    run_response = client.post('/api/v1/engagements/' + engagement.json()['id'] + '/start')
    assert run_response.status_code == 202
    run = run_response.json()['id']
    observation = client.post(f'/api/v1/runs/{run}/observations', json={
        'observation_type': 'http.authorization', 'subject': target,
        'summary': 'Local fixture candidate', 'source_capability': 'fixture',
    })
    assert observation.status_code == 201
    candidate = client.post(f'/api/v1/runs/{run}/candidates', json={
        'title': 'Cross identity local fixture', 'category': 'CWE-639', 'target': target,
        'hypothesis': 'Replay configured object boundary', 'observation_ids': [observation.json()['id']],
    })
    assert candidate.status_code == 201
    body = {'candidate_id': candidate.json()['id'], 'severity': 'unknown',
            'impact_description': 'Local fixture object read', 'root_cause': 'Unverified fixture cause',
            'weakness': 'CWE-639', 'location': target,
            'baseline': {'url': target, 'headers': {'X-Role': 'owner'}},
            'attack': {'url': target, 'headers': {'X-Role': 'other'}},
            'negative_control': {'url': target},
            'authorization': {
                'baseline_identity': {'url': origin + '/me', 'headers': {'X-Role': 'owner'}},
                'attack_identity': {'url': origin + '/me', 'headers': {'X-Role': 'other'}},
                'principal_field': 'id', 'owner_field': 'owner_id'}}
    job_id, now = core.uid('verify-job'), core.utcnow()
    with core.connect() as db:
        db.execute('INSERT INTO verification_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (job_id, body['candidate_id'], run, 'http-authorization-read-v2', 'running',
                    'Recovery fixture', 0, 10, 0, None, None, now, now, None))
    config = {'db': str(core.DB), 'artifacts': str(tmp_path / 'artifacts'),
              'run': run, 'job': job_id, 'body': body, 'complete': complete, 'leased': leased}
    process = subprocess.Popen([sys.executable, '-c', SUPERVISOR],
                               cwd=Path(__file__).resolve().parents[1],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        process.stdin.write(json.dumps(config) + '\n')
        process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=30), 'Supervisor did not reach a committed checkpoint'
        line = process.stdout.readline()
        assert line, process.stderr.read()
        checkpoint = json.loads(line)
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    expected = 10 if complete else 1
    assert checkpoint['completed_responses'] == expected
    assert len(calls) == expected
    with core.connect() as db:
        job_before = dict(db.execute('SELECT * FROM verification_jobs WHERE id=?', (job_id,)).fetchone())
        artifact = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (checkpoint['artifact_id'],)).fetchone())
    persisted = Path(artifact['uri']).read_bytes()
    assert hashlib.sha256(persisted).hexdigest() == checkpoint['artifact_sha256'] == artifact['sha256']
    material = json.loads(persisted)
    assert material['verification_complete'] is material['promotion_eligible'] is False
    assert 'headers' not in persisted.decode() and 'local fixture content' not in persisted.decode()
    assert bool(material['final_artifact_id']) == complete
    recovered = subprocess.run([sys.executable, '-c', RECOVERY],
                               input=json.dumps(config) + '\n', text=True, capture_output=True,
                               cwd=Path(__file__).resolve().parents[1], timeout=20, check=True)
    result = json.loads(recovered.stdout)
    assert result['job']['status'] == 'interrupted'
    assert result['job']['completed_requests'] == expected
    assert result['job']['result'] == job_before['result']
    assert result['run_status'] in {'paused', 'completed'}
    assert Path(artifact['uri']).read_bytes() == persisted
    assert len(calls) == expected  # Startup does not replay credentials or issue new HTTP.
    if leased:
        assert result['recovery'] == {'requeued': 1, 'failed': 0}
        assert result['tick']['completed'][0]['status'] == 'succeeded', result['tick']
        assert result['receipt']['result']['status'] == 'verified'
        assert result['receipt']['integrity']['promotion_eligible'] is True
        with core.connect() as db:
            task = dict(db.execute('SELECT * FROM agent_tasks WHERE id=?', (checkpoint['task_id'],)).fetchone())
        assert task['status'] == 'succeeded' and task['attempt'] == 2
        assert result['receipt']['environment']['process_isolation']['child_pid'] != process.pid
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0] == int(leased)
