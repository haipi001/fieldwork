import hashlib
import pytest
import final_core as core
from tests.test_final import client

SOURCE='import json,sys\nvalue=json.load(sys.stdin)\nprint(json.dumps({"response":"Reviewed local adapter"}))\n'


def setup(client):
    agent=client.post('/api/v1/workspace/agents',json={'name':'Adapter binding fixture'}).json()['id']
    return agent,'/api/v1/workspace/agent-attack-requests/adapters/'+agent


def register(client,url,source=SOURCE,enabled=False,expected=None):
    body={'source_code':source,'expected_configuration_sha256':expected,'enabled':enabled}
    plan=client.post(url+'/preview',json=body);assert plan.status_code==200,plan.text
    return client.put(url,json={**body,'preview_hash':plan.json()['preview_hash'],'confirmed':True})


def test_register_replace_enable_disable_without_execution_or_source_disclosure(client,monkeypatch):
    import agent_attack_executor
    monkeypatch.setattr(agent_attack_executor,'execute_local_adapter',lambda *args,**kwargs:pytest.fail('registration must not execute'))
    agent,url=setup(client)
    before=client.get(url).json();assert before['configured'] is False
    created=register(client,url);assert created.status_code==200,created.text
    value=created.json();assert value['configured'] and not value['enabled']
    assert value['source_sha256']==hashlib.sha256(SOURCE.encode()).hexdigest()
    assert SOURCE not in created.text and 'source_code' not in created.text
    enabled=client.put(url+'/state',json={'expected_configuration_sha256':value['configuration_sha256'],'enabled':True,'confirmed':True})
    assert enabled.status_code==200 and enabled.json()['enabled'] is True
    plan=client.post('/api/v1/workspace/agent-attack-requests/preview',json={'agent_id':agent,'category':'injection','max_cases':2}).json()
    assert plan['blockers']==['execution_plan_review_required'] and plan['execution_supported'] is False
    assert plan['adapter']['enabled'] is True and 'source_code' not in str(plan)
    current=enabled.json()
    stale=client.put(url+'/state',json={'expected_configuration_sha256':value['configuration_sha256'],'enabled':False,'confirmed':True})
    assert stale.status_code==409
    replacement=register(client,url,SOURCE+'# new version\n',False,current['configuration_sha256'])
    assert replacement.status_code==200 and not replacement.json()['enabled']
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone()[0]==1
        for table in ['agent_tasks','runtime_calls','analysis_runs','capability_grants_v6','agent_attack_tests_v5']:
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        assert all('source_code' not in row[0] for row in db.execute("SELECT payload_json FROM v5_events WHERE topic='agent_attack'"))


def test_registration_changes_invalidate_attack_request_preview(client):
    agent,url=setup(client)
    body={'agent_id':agent,'category':'injection','max_cases':2}
    preview=client.post('/api/v1/workspace/agent-attack-requests/preview',json=body).json()
    assert register(client,url).status_code==200
    saved=client.post('/api/v1/workspace/agent-attack-requests',json={**body,'preview_hash':preview['preview_hash'],'request_key':'stale-adapter-request','confirmed':True})
    assert saved.status_code==409


def test_invalid_source_bounds_confirmation_and_stale_configuration(client):
    agent,url=setup(client)
    for source in ['def invalid(', 'é'*40000]:
        assert client.post(url+'/preview',json={'source_code':source,'enabled':False}).status_code==422
    body={'source_code':SOURCE,'enabled':False}
    plan=client.post(url+'/preview',json=body).json()
    assert client.put(url,json={**body,'preview_hash':plan['preview_hash'],'confirmed':False}).status_code==422
    assert register(client,url).status_code==200
    assert client.put(url,json={**body,'preview_hash':plan['preview_hash'],'confirmed':True}).status_code==409
    assert client.get(url.replace(agent,'missing')).status_code==404
    with core.connect() as db:
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone()
        value=core.load(row['metadata_json']);value['source_code']+='tampered'
        db.execute('UPDATE agent_supply_components_v5 SET metadata_json=? WHERE id=?',(core.dump(value),row['id']))
    assert client.get(url).status_code==409


@pytest.mark.parametrize('category,cases,blocker',[('tool',2,'execution_plan_review_required'),('memory',1,'two_case_budget_required'),('memory',2,'execution_plan_review_required')])
def test_request_readiness_matches_supported_scenarios_and_case_budget(client,category,cases,blocker):
    agent,url=setup(client)
    assert register(client,url,enabled=True).status_code==200
    body={'agent_id':agent,'category':category,'max_cases':cases}
    base='/api/v1/workspace/agent-attack-requests'
    plan=client.post(base+'/preview',json=body).json()
    assert plan['blockers']==[blocker]
    assert plan['execution_plan_available']==(category in ('memory','tool') and cases==2)
    saved=client.post(base,json={**body,'preview_hash':plan['preview_hash'],'request_key':'readiness-fixture-request','confirmed':True})
    assert saved.status_code==201,saved.text
    assert saved.json()['status']==('awaiting_execution_review' if plan['execution_plan_available'] else 'blocked')
