import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import final_core
import v5_research_worker as research
from tests.test_final import client
from tests.test_v5_team_plan import _setup, _body
from tests.test_v5_workers import node


def team(client, count=8, *, with_evidence=True):
    _, campaign, run = _setup(client)
    if with_evidence:
        node(client, campaign['id'], 'evidence', 'Recorded access observation')
        node(client, campaign['id'], 'counterevidence', 'A legitimate shared resource exists')
    body = _body(campaign, run, count)
    if count == 8:
        body['roles'] = [{'role': 'researcher', 'count': 4}, {'role': 'explorer', 'count': 2}, {'role': 'specialist', 'count': 2}]
    return campaign, body


def create(client, body):
    preview = client.post('/api/v1/orchestration/groups/team/preview', json=body)
    assert preview.status_code == 200, preview.text
    response = client.post('/api/v1/orchestration/groups/team', json={**body,
        'preview_hash': preview.json()['preview_hash'], 'idempotency_key': 'actual-research-group'})
    assert response.status_code == 201, response.text
    return response.json()['group']['id']


def wait_group(group_id):
    research._jobs[group_id].join(timeout=10)
    assert not research._jobs[group_id].is_alive()


def test_eight_team_tasks_use_actual_model_http_and_preserve_draft_boundary(client):
    campaign, body = team(client)
    seen, active, peak = [], 0, 0
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal active, peak
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            context = json.loads(value['messages'][1]['content'])
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append(context)
            time.sleep(.2)
            support = [n['id'] for n in context['nodes'] if n['node_type'] == 'evidence']
            counters = [n['id'] for n in context['nodes'] if n['node_type'] == 'counterevidence']
            output = {'summary': 'Review of the recorded authorization observations', 'hypotheses': [{
                'statement': 'Recorded access may require a scoped ownership check', 'scope': 'Recorded fixture only',
                'evidence_ids': support, 'counterevidence_ids': counters, 'limitations': ['Not independently verified']}],
                'open_questions': ['Which sharing rule applies to this resource?']}
            raw = json.dumps({'choices': [{'message': {'content': json.dumps(output)}}],
                              'usage': {'prompt_tokens': 40, 'completion_tokens': 30}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            with lock:
                active -= 1
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with final_core.connect() as db:
            now = final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'research-model', 'llama_cpp', 'Research model fixture', f'http://127.0.0.1:{server.server_port}',
                'fixture', None, 1, '{"location":"local"}', 'healthy', now, now, now))
        profile_response = client.put('/api/v1/runtime/config', json={'name': 'Team profile', 'mode': 'offline',
            'config': {'local_provider_ids': ['research-model'], 'max_concurrent_calls': 2}})
        assert profile_response.status_code == 201, profile_response.text
        body['runtime_profile_id'] = profile_response.json()['id']
        group_id = create(client, body)
        start = client.post(f'/api/v1/workers/research/groups/{group_id}/start')
        assert start.status_code == 202 and start.json()['max_concurrency'] == 2, start.text
        duplicate = client.post(f'/api/v1/workers/research/groups/{group_id}/start')
        assert duplicate.status_code == 202 and duplicate.json()['status'] == 'already_running'
        wait_group(group_id)
        tasks = client.get(f'/api/v1/orchestration/tasks?campaign_id={campaign["id"]}').json()['items']
        assert len(seen) == 8 and peak == 2, [(t["status"],t["error"]) for t in tasks]
        assert all(t['status'] == 'succeeded' and t['result']['status'] == 'research_draft' for t in tasks)
        assert all(t['usage']['input_tokens'] == 40 and t['usage']['output_tokens'] == 30 for t in tasks)
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0] == 8
            assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=? AND source_type='team_research' AND node_type='claim'", (campaign['id'],)).fetchone()[0] == 8
            assert db.execute('SELECT COUNT(*) FROM verification_receipts_v5').fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE node_type='canonical_result'").fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0] == 0
    finally:
        research.stop_workers()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_team_rejects_model_invented_evidence_and_stale_frozen_input(client, monkeypatch):
    campaign, body = team(client, count=1)
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'research-model', 'llama_cpp', 'Fixture', 'http://127.0.0.1:1', 'fixture', None,
            1, '{"location":"local"}', 'healthy', now, now, now))
    group_id = create(client, body)
    monkeypatch.setattr(research.w, '_local_model_output', lambda *_: ({'summary': 'An untrusted research draft',
        'hypotheses': [{'statement': 'A hypothesis referencing invented evidence', 'scope': 'Fixture',
                        'evidence_ids': ['invented-id'], 'counterevidence_ids': []}], 'open_questions': []}, 10, 10, 5))
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 202
    wait_group(group_id)
    with final_core.connect() as db:
        assert db.execute("SELECT status FROM agent_tasks WHERE group_id=?", (group_id,)).fetchone()[0] == 'paused'
        assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE source_type='team_research'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0] == 1
        db.execute("UPDATE research_nodes SET body='changed material' WHERE campaign_id=? AND node_type='evidence'", (campaign['id'],))
    assert client.post(f'/api/v1/orchestration/groups/{group_id}/pause').status_code == 200
    assert client.post(f'/api/v1/orchestration/groups/{group_id}/resume').status_code == 200
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 409


def test_missing_local_provider_does_not_claim_team_tasks(client):
    _, body = team(client, count=1, with_evidence=False)
    group_id = create(client, body)
    response = client.post(f'/api/v1/workers/research/groups/{group_id}/start')
    assert response.status_code == 409, response.text
    with final_core.connect() as db:
        task = db.execute('SELECT status,attempt FROM agent_tasks WHERE group_id=?', (group_id,)).fetchone()
        assert tuple(task) == ('queued', 0)
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0] == 0


def test_pause_interrupts_active_model_and_retains_unknown_consumption(client):
    campaign, body = team(client, count=2)
    requested, disconnected = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '10000')
            self.end_headers()
            requested.set()
            try:
                for _ in range(200):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        with final_core.connect() as db:
            now = final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'pause-model', 'llama_cpp', 'Pause fixture', f'http://127.0.0.1:{server.server_port}',
                'fixture', None, 1, '{"location":"local"}', 'healthy', now, now, now))
        group_id = create(client, body)
        assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 202
        assert requested.wait(3)
        start = time.monotonic()
        assert client.post(f'/api/v1/orchestration/groups/{group_id}/pause').status_code == 200
        wait_group(group_id)
        assert time.monotonic() - start < 2 and disconnected.wait(1)
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status='paused'", (group_id,)).fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='unknown'").fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE source_type='team_research'").fetchone()[0] == 0
    finally:
        research.stop_workers()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def test_oversized_graph_is_rejected_before_call_reservation(client, monkeypatch):
    campaign, body = team(client, count=1)
    with final_core.connect() as db:
        db.execute("UPDATE research_nodes SET body=? WHERE campaign_id=? AND node_type='evidence'", ('x' * 40000, campaign['id']))
        now = final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'oversize-model', 'llama_cpp', 'Fixture', 'http://127.0.0.1:1', 'fixture', None,
            1, '{"location":"local"}', 'healthy', now, now, now))
    group_id = create(client, body)
    def forbidden(*_):
        raise AssertionError('model must not be called for oversized input')
    monkeypatch.setattr(research.w, '_local_model_output', forbidden)
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 202
    wait_group(group_id)
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0] == 0
        task = db.execute('SELECT status,error_json FROM agent_tasks WHERE group_id=?', (group_id,)).fetchone()
        assert task['status'] == 'paused' and json.loads(task['error_json'])['code'] == 'research_context_too_large'
