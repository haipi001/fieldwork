import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import final_core
import v5_runtime as runtime
from v5_model_response import decode_response, ModelOutputRejected
from tests.test_final import client
from tests.test_v5_research_worker import team, create, wait_group
from tests.test_v5_workers import setup_campaign, node, worker_task


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': True, 'completion_tokens': 1},
    {'prompt_tokens': 1.5, 'completion_tokens': 1}, {'prompt_tokens': -1, 'completion_tokens': 1},
    {'prompt_tokens': 2**70, 'completion_tokens': 1}])
def test_invalid_consumption_does_not_become_known_usage(usage):
    raw = json.dumps({'usage': usage, 'choices': [{'message': {'content': '{}'}}]}).encode()
    with pytest.raises(ValueError) as failure:
        decode_response(raw, 'llama_cpp', 10)
    assert not isinstance(failure.value, ModelOutputRejected)


@pytest.mark.parametrize('content', ['{"secret":"fixture-never-retain', '[]', None, '{"x":NaN}'])
def test_bad_content_retains_only_valid_usage_without_exception_body(content):
    raw = json.dumps({'usage': {'prompt_tokens': 17, 'completion_tokens': 12},
                     'choices': [{'message': {'content': content}}]}).encode()
    with pytest.raises(ModelOutputRejected) as failure:
        decode_response(raw, 'llama_cpp', 10)
    error = failure.value
    assert (error.input_tokens, error.output_tokens, error.runtime_ms) == (17, 12, 10)
    assert error.__context__ is None
    assert 'fixture-never-retain' not in str(error) + repr(vars(error))


@pytest.mark.parametrize('worker', ['team', 'structured'])
@pytest.mark.parametrize('kind', ['llama_cpp', 'ollama'])
@pytest.mark.parametrize('settlement_fails', [False, True])
def test_real_http_invalid_output_settles_known_usage_and_never_replays(client, monkeypatch, worker, kind, settlement_fails):
    hits = []
    marker = 'fixture-private-output-must-not-persist'
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            hits.append(self.path)
            content = '{"summary":"' + marker
            result = ({'message': {'content': content}, 'prompt_eval_count': 17, 'eval_count': 12}
                      if kind == 'ollama' else {'choices': [{'message': {'content': content}}],
                      'usage': {'prompt_tokens': 17, 'completion_tokens': 12}})
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with final_core.connect() as db:
            now = final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'bad-output-model', kind, 'Bad output HTTP fixture', f'http://127.0.0.1:{server.server_port}',
                'fixture', None, 1, '{"location":"local"}', 'healthy', now, now, now))
        if settlement_fails:
            def unavailable(_):
                raise RuntimeError('fixture ledger unavailable')
            monkeypatch.setattr(runtime, 'record_usage', unavailable)
        if worker == 'team':
            campaign, body = team(client, count=1)
            group = create(client, body)
            assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code == 202
            wait_group(group)
            tasks = client.get(f'/api/v1/orchestration/tasks?campaign_id={campaign["id"]}').json()['items']
            task_id = tasks[0]['id']
            assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code == 409
        else:
            _, cid = setup_campaign(client)
            claim = node(client, cid, 'claim', 'Bounded malformed output review')
            task_id = worker_task(client, cid, 'critic', [claim['id']], key='malformed-billing')['id']
            assert client.post('/api/v1/workers/local/tick?limit=2').status_code == 200
            assert client.post('/api/v1/workers/local/tick?limit=2').status_code == 200
        state = client.get(f'/api/v1/orchestration/tasks/{task_id}').json()
        assert len(hits) == 1 and state['status'] == 'paused' and state['result'] is None
        with final_core.connect() as db:
            call = db.execute('SELECT * FROM runtime_calls WHERE task_id=?', (task_id,)).fetchone()
            if settlement_fails:
                assert call['state'] == 'unknown'
                assert state['error']['code'] == 'model_usage_unknown'
                assert state['error']['settlement_error_type'] == 'RuntimeError'
                assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
            else:
                assert call['state'] == 'settled'
                assert state['error']['code'] == 'model_output_invalid_usage_settled'
                assert state['usage']['input_tokens'] == 17 and state['usage']['output_tokens'] == 12
                assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 1
                assert db.execute('SELECT COUNT(*) FROM runtime_call_timings').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE source_type='team_research'").fetchone()[0] == 0
            events = [row[0] for row in db.execute('SELECT payload_json FROM v5_events')]
            assert marker not in json.dumps(events) + json.dumps(state)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
