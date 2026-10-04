"""Real uvicorn cold starts against isolated persisted research material."""
import hashlib
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

import final_core as core
import traditional_runtime as http
from http_replay_checkpoints import persist
from tests.test_final import client, create_ready
from tests.test_http_business_boundary import object_server
from tests.test_v5_orchestration import task
from v5_http_transport import request_once
from version import APP_VERSION


SERVER = '''
import json, os, sys
from pathlib import Path
config = json.loads(sys.stdin.readline())
os.environ.pop('PYTEST_CURRENT_TEST', None)
os.environ.pop('SRC_ENABLE_SYNTHETIC_DEMO', None)
os.environ.pop('FIELDWORK_DISABLE_AGENT_MONITOR', None)
os.environ.pop('FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER', None)
os.environ.update(FIELDWORK_SESSION_TOKEN=config['token'], FIELDWORK_PORT=str(config['port']),
                  FIELDWORK_DESKTOP_INSTANCE=config['instance'])
import app, final_core, traditional_runtime, reporting, web3_analysis, traditional_tools
app.DATA = Path(config['data'])
app.DB = final_core.DB = Path(config['db'])
final_core.LOCAL_DATA_ROOT = app.DATA
traditional_runtime.ARTIFACT_ROOT = web3_analysis.ARTIFACT_ROOT = traditional_tools.ARTIFACT_ROOT = app.DATA / 'artifacts'
reporting.EXPORTS = app.DATA / 'exports'
import uvicorn
uvicorn.run(app.app, fd=config['fd'], log_level='error', access_log=False)
'''


def request(base, path, token=None, extra=None):
    headers = {'X-Fieldwork-Session': token} if token else {}
    headers.update(extra or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(base + path, headers=headers), timeout=1) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


@pytest.mark.parametrize('clean_start', range(3))
def test_real_asgi_cold_start_preserves_material_and_rotates_session(client, object_server, tmp_path, clean_start):
    origin, calls = object_server
    engagement = create_ready(client, target=origin)
    run_id, now = core.uid('run'), core.utcnow()
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs(id,engagement_id,mode,scope_snapshot_id,policy_id,status,current_stage,created_at) VALUES(?,?,?,?,?,?,?,?)',
                   (run_id, engagement['id'], 'traditional', engagement['current_scope_snapshot_id'],
                    engagement['current_policy_id'], 'running', 'fixture', now))
        run = dict(db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone())
    observation_id = core.uid('obs')
    with core.connect() as db:
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (observation_id, run_id, engagement['id'], 'traditional', 'http.authorization',
                    origin + '/object', 'Local fixture cold start material', 0.0, 'fixture', None, now))
    candidate = client.post(f'/api/v1/runs/{run_id}/candidates', json={
        'title': 'Cold start fixture', 'category': 'CWE-639', 'target': origin + '/object',
        'hypothesis': 'Partial material requires explicit reviewed replay',
        'observation_ids': [observation_id],
    })
    assert candidate.status_code == 201
    candidate_id = candidate.json()['id']
    observed = request_once(http.ReplayRequest(url=origin + '/object'), ('127.0.0.1',))
    checkpoint = persist(run, candidate_id, [{'negative_control': observed}], 10)
    artifact_path = tmp_path / 'artifacts' / (checkpoint['artifact_id'] + '.json')
    material = artifact_path.read_bytes()
    assert hashlib.sha256(material).hexdigest() == checkpoint['artifact_sha256']
    job_id, guided_id = core.uid('verify-job'), core.uid('guided')
    result_json = core.dump({'replay_checkpoint': checkpoint})
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        'name': 'Cold start campaign', 'objective': 'Persisted recovery fixture',
    })
    assert campaign.status_code == 201
    pending_task = task(client, campaign.json()['id'], 'cold-start-fixture')['id']
    with core.connect() as db:
        db.execute('INSERT INTO verification_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (job_id, candidate_id, run_id, 'http-authorization-read-v2', 'running',
                    'Fixture', 1, 10, 0, result_json, None, now, now, None))
        db.execute('INSERT INTO guided_research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (guided_id, run_id, candidate_id, 'fixture', 'running', 'Fixture',
                    core.dump([{'status': 'running', 'name': 'fixture'}]), result_json, 0, None, now, now))
        db.execute("UPDATE agent_tasks SET status='running',attempt=1,lease_owner='expired-fixture',lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pending_task,))
    old_token = None
    first_completion = None
    for restart in range(2):
        token, instance = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(32)
            port = listener.getsockname()[1]
            config = {'token': token, 'instance': instance, 'port': port, 'fd': listener.fileno(),
                      'data': str(tmp_path), 'db': str(core.DB)}
            process = subprocess.Popen([sys.executable, '-c', SERVER],
                                       cwd=Path(__file__).resolve().parents[1],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       pass_fds=(listener.fileno(),), text=True)
            try:
                process.stdin.write(json.dumps(config) + '\n')
                process.stdin.close()
                base = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, process.stderr.read()
                    try:
                        status, headers, body = request(base, '/health')
                        if status == 200:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    assert time.monotonic() < deadline, 'ASGI startup did not complete'
                    time.sleep(.05)
                assert json.loads(body) == {'status': 'ready'}
                assert headers['X-Fieldwork-Instance'] == instance
                assert headers['X-Fieldwork-Version'] == APP_VERSION
                assert request(base, '/v5')[0] == 401
                if old_token:
                    assert request(base, '/v5', old_token)[0] == 401
                assert request(base, '/v5', token)[0] == 200
                assert request(base, '/v5', token, {'Host': 'foreign.example'})[0] == 403
                assert request(base, '/v5', token, {'Origin': 'https://foreign.example'})[0] == 403
                while True:
                    status, _, body = request(base, '/api/v1/campaign-scheduler/status', token)
                    assert status == 200
                    scheduler = json.loads(body)
                    if scheduler['last_tick_at'] is not None:
                        break
                    assert time.monotonic() < deadline, 'Default scheduler did not tick'
                    time.sleep(.05)
                assert scheduler['running'] is True and scheduler['last_error'] is None
                assert scheduler['last_processed'] == 0
                status, _, body = request(base, '/api/v1/verification-jobs/' + job_id, token)
                assert status == 200
                job = json.loads(body)
                assert job['status'] == 'interrupted' and job['completed_requests'] == 1
                assert job['result']['replay_checkpoint'] == checkpoint
                if restart == 0:
                    first_completion = job['completed_at']
                else:
                    assert job['completed_at'] == first_completion
                status, _, body = request(base, '/api/v1/guided-research/' + guided_id, token)
                assert status == 200
                guided = json.loads(body)
                assert guided['status'] == 'interrupted'
                assert guided['steps'][0]['status'] == 'interrupted'
                assert guided['result']['replay_checkpoint'] == checkpoint
                assert artifact_path.read_bytes() == material
                assert len(calls) == 1
                with core.connect() as db:
                    recovered = db.execute('SELECT * FROM agent_tasks WHERE id=?', (pending_task,)).fetchone()
                    assert recovered['status'] == 'queued' and recovered['attempt'] == 1
                    assert recovered['lease_owner'] is recovered['lease_expires_at'] is None
                    assert db.execute('SELECT status FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0] == 'paused'
                    assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0] == 0
                    assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
                old_token = token
                if restart == 0:
                    process.kill()  # Real backend death followed by a fresh server process.
                else:
                    process.terminate()
                process.wait(5)
                diagnostics = process.stderr.read()
                assert 'Monitor scheduler failed' not in diagnostics
                assert 'ERROR:' not in diagnostics
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(5)
                process.stderr.close()
