"""Target-only preparation binds real execution inputs without granting authority."""
import json
import threading

import final_core
import native_agent
import traditional_tools
from tests.test_final import client


def setup(client, monkeypatch, target='https://assisted.example.test'):
    monkeypatch.setattr(final_core, 'capabilities', lambda: [{'id': 'httpx', 'available': True, 'configured': True}])
    monkeypatch.setattr(native_agent, 'readiness', lambda: {'ready': True, 'available': True, 'configured': True})
    project = client.post('/api/v1/engagements', json={'target': target}).json()
    with final_core.connect() as db:
        now=final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'assisted-local','llama_cpp','Isolated model','http://127.0.0.1:11434','fixture',None,1,
            '{"location":"local"}','healthy',now,now,now))
    profile = client.put('/api/v1/runtime/config', json={'name':'Existing local AI','mode':'local',
        'config':{'local_provider_ids':['assisted-local']}}).json()
    return project,profile


def prepared(client, project):
    response=client.get(f'/api/v1/engagements/{project["id"]}/assisted-plan')
    assert response.status_code==200,response.text
    return response.json()


def test_target_only_preparation_derives_name_and_ai_without_execution(client, monkeypatch):
    project,profile=setup(client,monkeypatch)
    assert project['name']=='Research · assisted.example.test'
    assert project['status']=='draft' and not project['confirmed_at']
    value=prepared(client,project)
    assert value['ai']['enabled'] and value['ai']['profile_id']==profile['id']
    assert value['execution']['include_native_agent'] is True
    assert value['execution']['execution_mode']=='real'
    assert value['plan']['ready'] is False and value['auto_start_ready'] is True
    assert value['plan']['budget']['requests']==100
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM analysis_runs').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
    response=client.post(f'/api/v1/engagements/{project["id"]}/start',json={**value['execution'],'assisted_plan_hash':value['plan_hash']})
    assert response.status_code==409


def test_authorized_start_passes_selected_ai_to_existing_pipeline(client, monkeypatch):
    project,profile=setup(client,monkeypatch)
    before=prepared(client,project)
    client.post(f'/api/v1/engagements/{project["id"]}/confirm',json={'scope_snapshot_id':project['current_scope_snapshot_id']})
    after=prepared(client,project)
    assert before['plan_hash']==after['plan_hash'] and after['plan']['ready']
    hits=[];dispatched=threading.Event()
    async def toolchain(run_id, body):
        hits.append((run_id,body.include_native_agent));dispatched.set()
    monkeypatch.setattr(traditional_tools,'execute_toolchain',toolchain)
    response=client.post(f'/api/v1/engagements/{project["id"]}/start',json={**before['execution'],'assisted_plan_hash':before['plan_hash']})
    assert response.status_code==202,response.text
    assert response.json()['synthetic'] is False
    assert dispatched.wait(2), 'existing execution pipeline did not receive the run'
    assert dispatched.wait(2), 'Queued pipeline was not dispatched'
    assert hits==[(response.json()['id'],True)]
    with final_core.connect() as db:
        config=json.loads(db.execute('SELECT config FROM run_configs_v2 WHERE run_id=?',(response.json()['id'],)).fetchone()[0])
    assert config['native_runtime_profile_id']==profile['id']
    assert config['reviewed_nuclei_templates']==next(item for item in before['plan']['tools'] if item['id']=='nuclei')['template_sha256']


def test_changed_budget_or_model_never_starts_stale_plan(client, monkeypatch):
    project,profile=setup(client,monkeypatch)
    value=prepared(client,project)
    client.post(f'/api/v1/engagements/{project["id"]}/confirm')
    with final_core.connect() as db:
        policy=db.execute('SELECT policy FROM execution_policies WHERE id=?',(project['current_policy_id'],)).fetchone()[0]
        policy=json.loads(policy);policy['max_requests']=200
        db.execute('UPDATE execution_policies SET policy=? WHERE id=?',(json.dumps(policy),project['current_policy_id']))
    response=client.post(f'/api/v1/engagements/{project["id"]}/start',json={**value['execution'],'assisted_plan_hash':value['plan_hash']})
    assert response.status_code==409 and 'changed' in response.json()['detail']
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM analysis_runs').fetchone()[0]==0
        db.execute("UPDATE runtime_providers SET enabled=0 WHERE id='assisted-local'")
    response=client.post(f'/api/v1/engagements/{project["id"]}/start',json={**value['execution'],'assisted_plan_hash':value['plan_hash']})
    assert response.status_code==409


def test_local_source_reused_and_no_cloud_or_missing_model_invented(client, monkeypatch, tmp_path):
    source=tmp_path/'source';source.mkdir()
    project=client.post('/api/v1/engagements',json={'target':str(source)}).json()
    monkeypatch.setattr(final_core,'capabilities',lambda:[{'id':'semgrep','available':True}])
    value=prepared(client,project)
    assert value['execution']['source_path']==str(source)
    assert not value['ai']['enabled'] and not value['execution']['include_native_agent']
    assert project['scope']['allowed_targets']==[str(source)]


def test_no_execution_capability_is_a_missing_input_not_success(client, monkeypatch):
    project=client.post('/api/v1/engagements',json={'target':'https://empty.example.test'}).json()
    monkeypatch.setattr(final_core,'capabilities',lambda:[])
    monkeypatch.setattr(native_agent,'readiness',lambda:{'ready':False})
    value=prepared(client,project)
    assert not value['auto_start_ready']
    assert any(item['id']=='capabilities' for item in value['missing'])


def test_target_mode_inferred_but_explicit_advanced_choice_preserved(client, tmp_path):
    address='0x'+'1'*40
    inferred=client.post('/api/v1/engagements',json={'target':address,'auto_mode':True}).json()
    assert inferred['mode']=='web3' and inferred['target_type']=='contract'
    website=client.post('/api/v1/engagements',json={'target':'https://automatic.example.test','mode':'web3','auto_mode':True}).json()
    assert website['mode']=='traditional'
    source=tmp_path/'solidity';source.mkdir();(source/'foundry.toml').write_text('[profile.default]')
    automatic=client.post('/api/v1/engagements',json={'target':str(source),'auto_mode':True}).json()
    explicit=client.post('/api/v1/engagements',json={'target':str(source),'auto_mode':False,'mode':'traditional'}).json()
    assert automatic['mode']=='web3' and explicit['mode']=='traditional'


def test_matched_model_without_browser_is_not_silently_skipped(client, monkeypatch):
    project,profile=setup(client,monkeypatch)
    monkeypatch.setattr(native_agent,'readiness',lambda:{'ready':False,'available':False,'configured':True})
    value=prepared(client,project)
    assert value['ai']['profile_id']==profile['id']
    assert not value['auto_start_ready']
    assert any(item['id']=='native-agent' for item in value['missing'])


def test_changed_template_bytes_invalidate_automatic_start_plan(client,monkeypatch,tmp_path):
    from pathlib import Path
    monkeypatch.setattr(Path,'home',lambda:tmp_path)
    template=tmp_path/'nuclei-templates/http/technologies/tech-detect.yaml'
    template.parent.mkdir(parents=True);template.write_text('initial template bytes')
    project,_=setup(client,monkeypatch)
    first=prepared(client,project)
    nuclei=next(item for item in first['plan']['tools'] if item['id']=='nuclei')
    assert set(nuclei['template_sha256'])=={'technologies/tech-detect.yaml'}
    assert str(tmp_path) not in json.dumps(nuclei['template_sha256'])
    client.post(f"/api/v1/engagements/{project['id']}/confirm")
    template.write_text('changed template bytes')
    second=prepared(client,project)
    assert second['plan_hash']!=first['plan_hash']
    response=client.post(f"/api/v1/engagements/{project['id']}/start",json={**first['execution'],'assisted_plan_hash':first['plan_hash']})
    assert response.status_code==409,response.text
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM analysis_runs').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
