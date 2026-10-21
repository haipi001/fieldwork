import pytest
import final_core as core
from tests.test_final import client


def setup(client):
    agent=client.post('/api/v1/workspace/agents',json={'name':'Isolated target record'}).json()
    body={'agent_id':agent['id'],'category':'injection','max_cases':3}
    return agent,body


def test_request_review_persistence_idempotence_and_cancel_without_execution(client):
    agent,body=setup(client)
    url='/api/v1/workspace/agent-attack-requests'
    with core.connect() as db:
        counts={t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['agent_tasks','runtime_calls','analysis_runs','verification_receipts_v5','capability_grants_v6']}
    plan=client.post(url+'/preview',json=body);assert plan.status_code==200,plan.text
    value=plan.json();assert value['agent_tested'] is False and value['execution_supported'] is False
    assert value['blockers']==['agent_execution_adapter_not_configured']
    commit={**body,'preview_hash':value['preview_hash'],'request_key':'isolated-request-1','confirmed':True}
    made=client.post(url,json=commit);assert made.status_code==201,made.text
    result=made.json();assert result['created'] and result['status']=='awaiting_adapter'
    assert client.post(url,json=commit).json()['created'] is False
    records=client.get('/api/v1/workspace/agents/attack').json()['items']
    assert len(records)==1 and records[0]['id']==result['id'] and records[0]['mode']=='reviewed_request'
    cancelled=client.post(url+'/'+result['id']+'/cancel',json={'preview_hash':value['preview_hash']})
    assert cancelled.status_code==200 and cancelled.json()['status']=='cancelled'
    assert client.post(url+'/'+result['id']+'/cancel',json={'preview_hash':value['preview_hash']}).status_code==200
    assert client.post(url,json=commit).json()['status']=='cancelled'
    with core.connect() as db:
        assert counts=={t:db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in counts}
        assert db.execute("SELECT count(*) FROM v5_events WHERE event_type='agent_attack.request_cancelled'").fetchone()[0]==1
        row=db.execute('SELECT * FROM agent_attack_tests_v5').fetchone()
        assert core.load(row['evidence_json'])==[] and row['completed_at']


@pytest.mark.parametrize('change',['agent','component'])
def test_changed_target_requires_new_preview(client,change):
    agent,body=setup(client);url='/api/v1/workspace/agent-attack-requests'
    value=client.post(url+'/preview',json=body).json()
    if change=='agent':
        with core.connect() as db:db.execute("UPDATE agent_registry_v5 SET status='revoked' WHERE id=?",(agent['id'],))
    else:
        response=client.post('/api/v1/workspace/components',json={'agent_id':agent['id'],'component_type':'mcp','name':'changed','version':'1','source':'fixture'})
        assert response.status_code==201,response.text
    result=client.post(url,json={**body,'preview_hash':value['preview_hash'],'request_key':'stale-request-1','confirmed':True})
    assert result.status_code==409,result.text
    with core.connect() as db:assert db.execute('SELECT count(*) FROM agent_attack_tests_v5').fetchone()[0]==0


@pytest.mark.parametrize('field,value',[('max_cases',True),('max_cases',101),('max_cases',0),('max_cases','3'),('category','arbitrary_command')])
def test_strict_bounds(client,field,value):
    _,body=setup(client)
    assert client.post('/api/v1/workspace/agent-attack-requests/preview',json={**body,field:value}).status_code==422


def test_missing_agent_confirmation_and_conflicting_request_key(client):
    _,body=setup(client);url='/api/v1/workspace/agent-attack-requests'
    assert client.post(url+'/preview',json={**body,'agent_id':'missing'}).status_code==404
    value=client.post(url+'/preview',json=body).json()
    commit={**body,'preview_hash':value['preview_hash'],'request_key':'conflict-request','confirmed':False}
    assert client.post(url,json=commit).status_code==422
    commit['confirmed']=True
    assert client.post(url,json=commit).status_code==201
    assert client.post(url,json={**commit,'max_cases':4}).status_code==409
