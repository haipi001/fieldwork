import json
import final_core
import v5_runtime as runtime
import v5_research_worker as research
import v5_workers
from tests.test_final import client
from tests.test_v5_research_worker import team, create, wait_group


def cloud_profile(client):
    with final_core.connect() as db:
        now=final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'public-cloud-fixture','openai_compatible','No-network cloud fixture','https://model.example.test/v1',
            'fixture',None,1,'{"location":"cloud","cost_micros_per_million_tokens":1000000}', 'healthy',now,now,now))
    response=client.put('/api/v1/runtime/config',json={'name':'Cloud research fixture','mode':'cloud',
        'config':{'cloud_provider_ids':['public-cloud-fixture'],'max_cost_micros':100000}})
    assert response.status_code==201,response.text
    return response.json()['id']


def test_cloud_optin_rejects_unclassified_and_sensitive_nodes(client):
    campaign,body=team(client,count=1)
    body.update(runtime_profile_id=cloud_profile(client),allow_cloud_context=True,max_cost_micros=100000)
    assert client.post('/api/v1/orchestration/groups/team/preview',json=body).status_code==409
    with final_core.connect() as db:
        db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',
                   (json.dumps({'sensitivity':'public','api_key':'fixture-never-transmit'}),campaign['id']))
    assert client.post('/api/v1/orchestration/groups/team/preview',json=body).status_code==409
    body['allow_cloud_context']=False
    group_id=create(client,body)
    task=client.get(f'/api/v1/orchestration/tasks?campaign_id={campaign["id"]}').json()['items'][0]
    decision=runtime.route_task(runtime.RouteRequest(task_id=task['id'],task_type='research',
        sensitivity='public',budget_remaining_micros=100000))
    assert decision['status']=='blocked' and decision['effective_sensitivity']=='secret'
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code==409
    with final_core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0


def test_public_optin_uses_profile_cloud_route_and_configured_rate(client,monkeypatch):
    campaign,body=team(client,count=1)
    body.update(runtime_profile_id=cloud_profile(client),allow_cloud_context=True,max_cost_micros=100000)
    with final_core.connect() as db:
        db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',
                   (json.dumps({'sensitivity':'public'}),campaign['id']))
    group_id=create(client,body)
    seen=[]
    def model(provider,task,nodes):
        seen.append((provider['id'],json.loads(task['context_capsule_json'])['cloud_context_approved']))
        return ({'summary':'Public graph analysis fixture','hypotheses':[],
                 'open_questions':['Which public evidence should be collected next?']},40,30,5)
    monkeypatch.setattr(research.w,'_local_model_output',model)
    response=client.post(f'/api/v1/workers/research/groups/{group_id}/start')
    assert response.status_code==202,response.text
    wait_group(group_id)
    assert seen==[('public-cloud-fixture',True)]
    with final_core.connect() as db:
        usage=db.execute('SELECT * FROM runtime_usage').fetchone()
        assert usage['route']=='cloud' and usage['cost_micros']==70
        assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0


def test_cloud_response_cannot_echo_provider_credential(monkeypatch):
    import runtime_secrets
    import v5_model_transport
    credential = 'fixture-opaque-provider-credential'
    monkeypatch.setattr(runtime_secrets, 'get', lambda reference: credential)
    echoed = {'summary': 'Echo ' + credential,
              'hypotheses': [{'statement': credential, 'limitations': [credential]}],
              'open_questions': [credential], credential: {'nested': credential}}
    seen = []
    def request(url, payload, timeout_ms, check_current, **options):
        assert options == {'location': 'cloud', 'api_key': credential}
        assert credential not in json.dumps(payload)
        seen.append(url)
        return json.dumps({'choices': [{'message': {'content': json.dumps(echoed)}}],
                           'usage': {'prompt_tokens': 7, 'completion_tokens': 11}}).encode()
    monkeypatch.setattr(v5_model_transport, 'request_model', request)
    provider = {'kind': 'openai_compatible', 'base_url': 'https://model.example.test/v1',
                'model': 'fixture', 'secret_ref': 'fixture-reference',
                'metadata_json': '{"location":"cloud"}'}
    task = {'role': 'researcher', 'objective': 'Analyze public fixture',
            'budget_json': '{"max_tokens":4000,"max_runtime_ms":1000}',
            'context_capsule_json': '{"team_plan":true,"cloud_context_approved":true,"sensitivity":"public"}'}
    output, input_tokens, output_tokens, _ = v5_workers._local_model_output(provider, task, [])
    assert credential not in json.dumps(output)
    assert '[REDACTED]' in json.dumps(output)
    assert (input_tokens, output_tokens) == (7, 11)
    assert seen == ['https://model.example.test/v1/chat/completions']


def test_cloud_transport_diagnostic_preserves_unknown_budget(client, monkeypatch):
    from v5_model_transport import ModelTransportError
    campaign, body = team(client, count=1)
    body.update(runtime_profile_id=cloud_profile(client), allow_cloud_context=True, max_cost_micros=100000)
    with final_core.connect() as db:
        db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',
                   ('{"sensitivity":"public"}', campaign['id']))
    group_id = create(client, body)
    def rejected(*_):
        raise ModelTransportError('http_status_401')
    monkeypatch.setattr(research.w, '_local_model_output', rejected)
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 202
    wait_group(group_id)
    with final_core.connect() as db:
        task = db.execute('SELECT status,error_json FROM agent_tasks WHERE group_id=?', (group_id,)).fetchone()
        assert task['status'] == 'paused'
        assert json.loads(task['error_json'])['transport_code'] == 'http_status_401'
        assert json.loads(task['error_json'])['code'] == 'model_usage_unknown'
        assert db.execute('SELECT state FROM runtime_calls').fetchone()[0] == 'unknown'
        assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
    assert client.post(f'/api/v1/workers/research/groups/{group_id}/start').status_code == 409
