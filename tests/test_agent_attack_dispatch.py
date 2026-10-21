import hashlib,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import pytest
import final_core as core
from tests.test_final import client
from tests.test_agent_attack_adapters import register,SOURCE


def setup(client,source=SOURCE,category='injection'):
    audit=client.post('/api/v1/agent-audit/audits',json={'name':'Local synthetic adapter fixture','agent_name':'Fixture agent','task_objective':'Test synthetic marker locally','session_id':'local-fixture-session','start_time':'2026-10-19T00:00:00Z','end_time':'2026-10-19T01:00:00Z'} )
    assert audit.status_code==201,audit.text
    audit=audit.json()
    agent=client.post('/api/v1/workspace/agents',json={'name':'Local fixture adapter'}).json()['id']
    assert register(client,'/api/v1/workspace/agent-attack-requests/adapters/'+agent,source,True).status_code==200
    base='/api/v1/workspace/agent-attack-requests'
    body={'agent_id':agent,'category':category,'max_cases':2}
    preview=client.post(base+'/preview',json=body).json()
    req=client.post(base,json={**body,'preview_hash':preview['preview_hash'],'request_key':'local-dispatch-fixture','confirmed':True})
    assert req.status_code==201,req.text
    url=base+'/'+req.json()['id']
    inputs={'engagement_id':audit['id'],'timeout_seconds':1,'authorization_note':'Explicit fixture-only local source approval'}
    planned=client.post(url+'/execution-plan',json=inputs);assert planned.status_code==200,planned.text
    return audit,agent,url,inputs,planned.json()


def test_real_dispatch_creates_scoped_synthetic_run_and_observation_not_finding(client):
    audit,agent,url,inputs,plan=setup(client)
    with core.connect() as db:
        original_run=dict(db.execute('SELECT * FROM analysis_runs WHERE id=?',(audit['run_id'],)).fetchone())
        original_scope=dict(db.execute('SELECT * FROM scope_snapshots WHERE id=?',(plan['current_scope_snapshot_id'],)).fetchone())
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==200,result.text
    value=result.json();assert value['status']=='observed' and value['case_count']==2 and not value['finding_issued']
    with core.connect() as db:
        assert dict(db.execute('SELECT * FROM analysis_runs WHERE id=?',(audit['run_id'],)).fetchone())==original_run
        assert dict(db.execute('SELECT * FROM scope_snapshots WHERE id=?',(original_scope['id'],)).fetchone())==original_scope
        run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(value['run_id'],)).fetchone()
        assert run['synthetic']==1 and run['status']=='completed' and run['scope_snapshot_id']!=original_scope['id']
        scope=db.execute('SELECT * FROM scope_snapshots WHERE id=?',(run['scope_snapshot_id'],)).fetchone()
        grant=core.load(scope['rules'])['local_synthetic_adapter_test'];assert grant['agent_id']==agent and grant['tool_permissions']==[] and not grant['network_allowed']
        budget=db.execute('SELECT * FROM run_budgets_v2 WHERE run_id=?',(run['id'],)).fetchone()
        assert budget['tool_calls_used']==budget['tool_call_limit']==2 and budget['request_limit']==0 and budget['model_budget_micros']==0
        artifact=db.execute('SELECT * FROM artifacts WHERE id=?',(value['artifact_id'],)).fetchone()
        raw=Path(artifact['uri']).read_bytes();assert hashlib.sha256(raw).hexdigest()==artifact['sha256']
        assert Path(artifact['uri']).stat().st_mode&0o777==0o600
        report=json.loads(raw);assert report['result']['control_clean'] and not report['result']['attack_specific_disclosure']
        assert report['status']==value['status']
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        before=db.execute('SELECT count(*) FROM scope_snapshots').fetchone()[0]
    assert client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True}).status_code==409
    with core.connect() as db:assert db.execute('SELECT count(*) FROM scope_snapshots').fetchone()[0]==before
    evidence=client.get(url+'/evidence');assert evidence.status_code==200,evidence.text
    assert evidence.json()['run_id']==value['run_id'] and evidence.json()['integrity_verified'] and evidence.json()['current_authorization_and_adapter']
    Path(artifact['uri']).write_text('tampered observation')
    assert client.get(url+'/evidence').status_code==409


def test_scope_or_adapter_change_invalidates_execution_plan(client):
    audit,agent,url,inputs,plan=setup(client)
    with core.connect() as db:
        db.execute("UPDATE scope_snapshots SET source='changed authorization' WHERE id=?",(plan['current_scope_snapshot_id'],))
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==409,result.text
    assert client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':False}).status_code==422


def test_real_running_cancellation(client):
    audit,agent,url,inputs,plan=setup(client,'import time\ntime.sleep(10)')
    inputs['timeout_seconds']=5
    plan=client.post(url+'/execution-plan',json=inputs).json()
    with ThreadPoolExecutor(max_workers=1) as workers:
        future=workers.submit(client.post,url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if client.get(url+'/execution').json()['status']=='executing':break
            time.sleep(.03)
        assert client.post(url+'/execution-cancel').status_code==200
        result=future.result(timeout=5)
    assert result.status_code==200,result.text
    assert result.json()['status']=='cancelled' and result.json()['case_count']<=1


def test_interrupted_recovery_requires_no_active_lock_and_never_replays(client,monkeypatch):
    import agent_attack_dispatch as dispatch
    import agent_attack_executor
    audit,agent,url,inputs,plan=setup(client)
    class SimulatedExecutorDeath(BaseException):pass
    monkeypatch.setattr(agent_attack_executor,'execute_local_adapter',lambda *args,**kwargs:(_ for _ in ()).throw(SimulatedExecutorDeath()))
    with pytest.raises(SimulatedExecutorDeath):
        dispatch.execute(url.rsplit('/',1)[-1],dispatch.Execute(**inputs,plan_hash=plan['plan_hash'],confirmed=True))
    assert client.get(url+'/execution').json()['status']=='executing'
    with dispatch.execution_lock():
        assert client.post(url+'/recover-interrupted').status_code==409
    recovered=client.post(url+'/recover-interrupted');assert recovered.status_code==200,recovered.text
    assert recovered.json()['status']=='interrupted' and not recovered.json()['automatic_replay']
    assert client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True}).status_code==409


@pytest.mark.parametrize('change',['agent','run','budget'])
def test_live_authorization_or_run_budget_change_stops_execution(client,change):
    audit,agent,url,inputs,plan=setup(client,'import time\ntime.sleep(10)')
    inputs['timeout_seconds']=5
    plan=client.post(url+'/execution-plan',json=inputs).json()
    with ThreadPoolExecutor(max_workers=1) as workers:
        future=workers.submit(client.post,url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
        deadline=time.monotonic()+3;run_id=None
        while time.monotonic()<deadline:
            state=client.get(url+'/execution').json()
            if state.get('execution'):
                run_id=state['execution']['run_id']
                with core.connect() as db:
                    if db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(run_id,)).fetchone()[0]==1:break
            time.sleep(.03)
        assert run_id
        with core.connect() as db:
            if change=='agent':db.execute("UPDATE agent_registry_v5 SET status='revoked' WHERE id=?",(agent,))
            elif change=='run':db.execute("UPDATE analysis_runs SET status='cancelled' WHERE id=?",(run_id,))
            else:db.execute('UPDATE run_budgets_v2 SET tool_call_limit=0 WHERE run_id=?',(run_id,))
        result=future.result(timeout=5)
    assert result.status_code==200,result.text
    assert result.json()['status']=='cancelled' and result.json()['case_count']<=1


def test_result_database_failure_cleans_owned_file_and_requires_explicit_recovery(client):
    import sqlite3
    audit,agent,url,inputs,plan=setup(client)
    root=core.LOCAL_DATA_ROOT/'agent_adapter_results'
    before=set(root.glob('*.json'))
    with core.connect() as db:
        db.execute("CREATE TRIGGER reject_adapter_result BEFORE INSERT ON artifacts WHEN NEW.kind='agent.local_adapter_observation' BEGIN SELECT RAISE(ABORT,'injected result registration failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert set(root.glob('*.json'))==before
    assert client.get(url+'/execution').json()['status']=='executing'
    assert client.post(url+'/recover-interrupted').json()['status']=='interrupted'


@pytest.mark.parametrize('polluted',[False,True])
def test_real_memory_scenario_dispatch_and_registered_observation(client,polluted):
    source='''import json,sys,pathlib
value=json.load(sys.stdin)
path=pathlib.Path(value['memory']['file_path'])
memory=json.loads(path.read_text())
'''
    if polluted:source+="if value['untrusted_document'].startswith('Ignore the task'):memory['policy']={'network_access':'allowed'}\n"
    else:source+="memory['notes'].append('benign summary')\n"
    source+="path.write_text(json.dumps(memory))\nprint(json.dumps({'response':'No policy change claimed.'}))\n"
    audit,agent,url,inputs,plan=setup(client,source,category='memory')
    assert plan['scenario_id']=='memory-protected-policy.v1'
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['status']=='observed' and result.json()['case_count']==2
    evidence=client.get(url+'/evidence');assert evidence.status_code==200,evidence.text
    observed=evidence.json()['observation']['result']
    assert observed['attack_specific_memory_mutation']==polluted
    assert observed['cases'][1]['memory_observation']['observed_by']=='fieldwork_local_supervisor'
    assert not evidence.json()['independently_verified'] and not evidence.json()['finding_issued']
    with core.connect() as db:
        row=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(url.rsplit('/',1)[-1],)).fetchone()
        assert core.load(row['result_json'])['execution']['observation']['attack_specific_memory_mutation']==polluted
        scope=db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(core.load(row['result_json'])['execution']['scope_snapshot_id'],)).fetchone()
        assert core.load(scope[0])['local_synthetic_adapter_test']['scenario_id']=='memory-protected-policy.v1'
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0


@pytest.mark.parametrize('behavior',['protected','forbidden_resource','forbidden_tool','self_report_only'])
def test_real_tool_request_dispatch_records_broker_decisions_not_host_bypass(client,behavior):
    from tests.test_agent_attack_executor import tool_adapter_source
    audit,agent,url,inputs,plan=setup(client,tool_adapter_source(behavior),category='tool')
    assert plan['scenario_id']=='synthetic-tool-request-boundary.v1'
    assert plan['adapter_protocol']=='fieldwork.local-agent-tools-jsonl.v1'
    assert plan['max_synthetic_broker_calls']==8 and plan['host_tool_permissions']==[]
    assert plan['tool_permissions']==[{'name':'fixture.read','allowed_resources':['fixture://public'],'max_calls_per_case':4,'synthetic_only':True}]
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['status']=='observed' and result.json()['case_count']==2
    proof=client.get(url+'/evidence');assert proof.status_code==200,proof.text
    observed=proof.json()['observation']['result']
    assert observed['attack_specific_forbidden_tool_request']==(behavior in ('forbidden_resource','forbidden_tool'))
    for case in observed['cases']:
        assert case['tool_observation']['broker_policy_bypass_observed'] is False
        for call in case['tool_observation']['calls']:
            if not call['response']['allowed']:assert 'result' not in call['response']
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0]==0
        row=db.execute('SELECT result_json FROM agent_attack_tests_v5 WHERE id=?',(url.rsplit('/',1)[-1],)).fetchone()
        execution=core.load(row[0])['execution']
        assert execution['observation']['attack_specific_forbidden_tool_request']==observed['attack_specific_forbidden_tool_request']
        grant=core.load(db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(execution['scope_snapshot_id'],)).fetchone()[0])['local_synthetic_adapter_test']
        assert grant['tool_permissions']==plan['tool_permissions']
