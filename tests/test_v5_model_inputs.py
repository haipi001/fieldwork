import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import final_core
from tests.test_final import client
from tests.test_v5_research_worker import team, create, wait_group


@pytest.mark.parametrize('count,total,cap', [(10,1000,None),(10000,1000,None),(True,1000,None),(10,20000,9000)])
def test_server_count_limits_generation_before_dispatch(client, count,total,cap):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, payload))
            if self.path.endswith('/input_tokens'):
                result = {'object': 'response.input_tokens', 'input_tokens': count}
            else:
                result = {'choices': [{'message': {'content': json.dumps({
                    'summary': 'Bounded counted-input research fixture', 'hypotheses': [],
                    'open_questions': ['Which authorization rule applies?']})}}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 7}}
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        campaign, body = team(client, count=1)
        body['max_tokens_per_task'] = total
        with final_core.connect() as db:
            now = final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'counted-model', 'llama_cpp', 'Count protocol fixture', f'http://127.0.0.1:{server.server_port}',
                'fixture', None, 1, json.dumps({'location':'local','input_token_counting':'llama_cpp_server'}|({'max_output_tokens':cap} if cap is not None else {})),
                'healthy', now, now, now))
        if cap is not None:
            configured=client.put('/api/v1/runtime/config',json={'name':'Output cap protocol','mode':'offline','config':{'local_provider_ids':['counted-model'],'max_tokens_per_call':total,'max_tokens_per_hour':total}})
            assert configured.status_code==201,configured.text
            body['runtime_profile_id']=configured.json()['id']
        group = create(client, body)
        assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code == 202
        wait_group(group)
        tasks = client.get(f'/api/v1/orchestration/tasks?campaign_id={campaign["id"]}').json()['items']
        assert calls[0][0] == '/v1/chat/completions/input_tokens'
        with final_core.connect() as db:
            if type(count) is int and count == 10:
                assert tasks[0]['status'] == 'succeeded'
                assert len(calls) == 2 and calls[1][0] == '/v1/chat/completions'
                assert calls[1][1]['max_tokens'] == (cap or 990)
                assert calls[0][1]['messages'] == calls[1][1]['messages']
                recorded = client.get('/api/v1/runtime/calls', params={'campaign_id': campaign['id']}).json()['items'][0]
                assert recorded['input_counting'] == 'llama_cpp_server'
                assert recorded['preflight_input_tokens'] == 10 and recorded['output_max_tokens'] == (cap or 990)
                preflight = db.execute('SELECT * FROM runtime_call_inputs').fetchone()
                assert preflight['strategy'] == 'llama_cpp_server' and preflight['input_tokens'] == 10
                with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                    db.execute('UPDATE runtime_call_inputs SET input_tokens=1')
                assert db.execute('SELECT state FROM runtime_calls').fetchone()[0] == 'settled'
            else:
                assert len(calls) == 1 and tasks[0]['status'] == 'paused'
                assert db.execute('SELECT state FROM runtime_calls').fetchone()[0] == 'unknown'
                assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0] == 0
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_input_estimate_blocks_without_model_request(client, monkeypatch):
    import v5_model_transport
    monkeypatch.setattr(v5_model_transport, 'request_model', lambda *_args, **_kwargs: pytest.fail('must not send'))
    campaign, body = team(client, count=1)
    body['max_tokens_per_task'] = 50
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'estimate-model', 'llama_cpp', 'No-request fixture', 'http://127.0.0.1:1', 'fixture', None,
            1, '{"location":"local"}', 'healthy', now, now, now))
    group = create(client, body)
    assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code == 202
    wait_group(group)
    with final_core.connect() as db:
        assert db.execute('SELECT state FROM runtime_calls').fetchone()[0] == 'released'
        assert db.execute('SELECT COUNT(*) FROM runtime_call_inputs').fetchone()[0] == 0
    tasks = client.get(f'/api/v1/orchestration/tasks?campaign_id={campaign["id"]}').json()['items']
    assert tasks[0]['status'] == 'paused'


@pytest.mark.parametrize('location,kind,base', [('cloud','llama_cpp','https://model.example.test'),
    ('local','ollama','http://127.0.0.1:11434')])
def test_counting_contract_rejects_other_provider_types(client, location, kind, base):
    response = client.post('/api/v1/runtime/providers', json={'name':'Bad count contract',
        'location':location,'kind':kind,'base_url':base,'model':'fixture',
        'metadata':{'input_token_counting':'llama_cpp_server'}})
    assert response.status_code == 422


@pytest.mark.parametrize('cap,total,context,expected',[(None,20000,32768,4000),(9000,20000,32768,9000),(9000,1000,32768,None),(9000,20000,1000,None)])
def test_configured_output_cap_stays_within_total_and_context(cap,total,context,expected):
    from v5_model_inputs import input_plan
    metadata={'max_context_tokens':context}
    if cap is not None:metadata['max_output_tokens']=cap
    row={'model':'fixture','metadata_json':json.dumps(metadata)}
    result=input_plan(row,[{'role':'user','content':'bounded fixture'}],total)
    assert result['output_max_tokens']==(expected if expected is not None else min(total,context)-result['input_tokens'])
    assert result['input_tokens']+result['output_max_tokens']<=min(total,context)


@pytest.mark.parametrize('value',[True,0,-1,100001,'8000'])
def test_invalid_output_cap_never_dispatches(value):
    from v5_model_inputs import input_plan
    with pytest.raises(ValueError,match='output token cap'):
        input_plan({'model':'fixture','metadata_json':json.dumps({'max_output_tokens':value})},[{'role':'user','content':'bounded'}],20000)


def test_output_cap_patch_preserves_cost_and_invalidates_bound_profile(client):
    from tests.test_v5_runtime import provider,profile
    source=provider(client,'output-cap','local')
    frozen=profile(client,'offline',local=[source['id']])
    assert source['metadata']['max_output_tokens']==4000
    changed=client.patch('/api/v1/runtime/providers/'+source['id'],json={'max_output_tokens':12000,'cost_micros_per_million_tokens':1200000})
    assert changed.status_code==200,changed.text
    value=changed.json()
    assert value['metadata']['max_output_tokens']==12000 and value['metadata']['cost_micros_per_million_tokens']==1200000
    assert value['metadata']['priority']==source['metadata']['priority'] and value['model']==source['model']
    assert value['configuration_sha256']!=source['configuration_sha256']
    profile_row=next(row for row in client.get('/api/v1/runtime/config').json()['profiles'] if row['id']==frozen['id'])
    assert profile_row['provider_configuration_status']=='stale'
    assert client.patch('/api/v1/runtime/providers/'+source['id'],json={'max_output_tokens':True}).status_code==422
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
