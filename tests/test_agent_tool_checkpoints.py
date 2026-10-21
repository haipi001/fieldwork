import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
import final_core as core
from tests.test_final import client
from tests.test_agent_attack_dispatch import setup
from tests.test_agent_attack_executor import tool_adapter_source

WAITING='''import json,sys,time
value=json.loads(sys.stdin.readline())
print(json.dumps({'type':'tool_call','id':'public','name':'fixture.read','arguments':{'resource':'fixture://public'}}),flush=True)
reply=json.loads(sys.stdin.readline())
time.sleep(30)
print(json.dumps({'type':'final','response':reply['result']['receipt']}),flush=True)
'''


def test_complete_tool_checkpoints_are_hash_checked_and_registered_in_artifact(client):
    audit,agent,url,inputs,plan=setup(client,tool_adapter_source('forbidden_resource'),category='tool')
    response=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert response.status_code==200,response.text
    saved=client.get(url+'/checkpoints');assert saved.status_code==200,saved.text
    data=saved.json();assert data['known_request_count']==3 and data['actual_total_request_count'] is None
    assert data['adapter_invocations_used']==2
    assert all(record['response_delivery']=='written_to_pipe' for record in data['checkpoints'])
    assert data['checkpoints'][1]['call']['response']['allowed'] is False
    artifact=client.get(url+'/evidence').json()['observation']
    assert artifact['tool_checkpoints']==data['checkpoints']
    for case in artifact['result']['cases']:
        assert case['tool_observation']['checkpoint_persistence']=='external_callback'
    with core.connect() as db:
        row=db.execute('SELECT result_json FROM agent_attack_tests_v5 WHERE id=?',(url.rsplit('/',1)[-1],)).fetchone()
        value=core.load(row[0]);value['execution']['tool_checkpoints'][0]['call']['request']['arguments']['resource']='fixture://private'
        db.execute('UPDATE agent_attack_tests_v5 SET result_json=? WHERE id=?',(core.dump(value),url.rsplit('/',1)[-1]))
    assert client.get(url+'/checkpoints').status_code==409


def test_real_cancel_preserves_previously_committed_tool_call(client):
    audit,agent,url,inputs,plan=setup(client,WAITING,category='tool')
    inputs['timeout_seconds']=5;plan=client.post(url+'/execution-plan',json=inputs).json()
    with ThreadPoolExecutor(max_workers=1) as workers:
        future=workers.submit(client.post,url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
        deadline=time.monotonic()+3;before=None
        while time.monotonic()<deadline:
            current=client.get(url+'/checkpoints').json()
            if current['checkpoints'] and current['checkpoints'][0]['response_delivery']=='written_to_pipe':before=current;break
            if future.done():
                future.result()
                pytest.fail('execution ended before checkpoint')
            time.sleep(.03)
        assert before and before['known_request_count']==1
        assert client.post(url+'/execution-cancel').status_code==200
        result=future.result(timeout=5);assert result.status_code==200,result.text
    assert result.json()['status']=='cancelled' and result.json()['known_tool_requests']==1
    after=client.get(url+'/checkpoints').json()
    assert after['checkpoints']==before['checkpoints'] and after['actual_total_request_count'] is None
    artifact=client.get(url+'/evidence').json()['observation']
    assert artifact['tool_checkpoints']==after['checkpoints']
    assert artifact['result']['cases'][0]['tool_observation']['call_count']==1


def test_real_executor_parent_death_retains_checkpoint_and_recovery_never_replays(client,tmp_path):
    audit,agent,url,inputs,plan=setup(client,WAITING,category='tool')
    inputs['timeout_seconds']=15;plan=client.post(url+'/execution-plan',json=inputs).json()
    repo=Path(__file__).resolve().parents[1];request_id=url.rsplit('/',1)[-1]
    body={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True}
    script=tmp_path/'owned_checkpoint_parent.py'
    script.write_text('import sys\nfrom pathlib import Path\nsys.path[:0]='+repr([str(repo/'backend'),str(repo)])+'\nimport final_core as core\ncore.DB=Path('+repr(str(core.DB))+')\ncore.LOCAL_DATA_ROOT=Path('+repr(str(core.LOCAL_DATA_ROOT))+')\nfrom agent_attack_dispatch import execute,Execute\nexecute('+repr(request_id)+',Execute(**'+repr(body)+'))\n')
    parent=subprocess.Popen([sys.executable,str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5;before=None
        while time.monotonic()<deadline:
            current=client.get(url+'/checkpoints').json()
            if current['checkpoints'] and current['checkpoints'][0]['response_delivery']=='written_to_pipe':before=current;break
            assert parent.poll() is None,'owned executor exited before checkpoint'
            time.sleep(.03)
        assert before and parent.poll() is None
        parent.kill();parent.wait(timeout=5)
        after=client.get(url+'/checkpoints').json()
        assert after['status']=='executing' and after['checkpoints']==before['checkpoints']
        recovered=client.post(url+'/recover-interrupted');assert recovered.status_code==200,recovered.text
        assert not recovered.json()['automatic_replay']
        retained=client.get(url+'/checkpoints').json()
        assert retained['status']=='interrupted' and retained['checkpoints']==before['checkpoints']
        assert client.post(url+'/execute',json=body).status_code==409
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=5)
        if parent.stderr:parent.stderr.close()


def test_concurrent_checkpoint_reads_keep_owned_storage_and_records(client):
    audit,agent,url,inputs,plan=setup(client,WAITING,category='tool')
    inputs['timeout_seconds']=15;plan=client.post(url+'/execution-plan',json=inputs).json()
    with ThreadPoolExecutor(max_workers=1) as executors:
        future=executors.submit(client.post,url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
        deadline=time.monotonic()+3;before=None
        while time.monotonic()<deadline:
            current=client.get(url+'/checkpoints').json()
            if current['checkpoints'] and current['checkpoints'][0]['response_delivery']=='written_to_pipe':before=current;break
            assert not future.done()
            time.sleep(.03)
        assert before
        root=core.LOCAL_DATA_ROOT;identity=root.stat()
        def read(_):
            response=client.get(url+'/checkpoints')
            assert response.status_code==200,response.text
            assert response.json()['checkpoints']==before['checkpoints']
        with ThreadPoolExecutor(max_workers=8) as readers:list(readers.map(read,range(64)))
        current=root.stat();assert (current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino)
        assert client.post(url+'/execution-cancel').status_code==200
        result=future.result(timeout=5);assert result.status_code==200,result.text
        assert result.json()['status']=='cancelled'


def test_checkpoint_write_failure_withholds_response_and_keeps_total_unknown(client):
    audit,agent,url,inputs,plan=setup(client,tool_adapter_source('protected'),category='tool')
    with core.connect() as db:
        db.execute("CREATE TRIGGER reject_prepared_checkpoint BEFORE INSERT ON v5_events WHEN NEW.event_type='agent_attack.tool_response_prepared' BEGIN SELECT RAISE(ABORT,'injected checkpoint write failure'); END")
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['status']=='incomplete'
    saved=client.get(url+'/checkpoints').json()
    assert saved['known_request_count']==0 and saved['actual_total_request_count'] is None
    assert saved['adapter_invocations_used']==1
    with core.connect() as db:
        assert db.execute("SELECT count(*) FROM v5_events WHERE event_type='agent_attack.tool_response_delivered'").fetchone()[0]==0
    assert client.get(url+'/evidence').json()['observation']['error_type']=='IntegrityError'


def test_revocation_after_request_preparation_is_saved_but_not_acknowledged(client,monkeypatch):
    import agent_tool_checkpoints as checkpoints
    audit,agent,url,inputs,plan=setup(client,tool_adapter_source('protected'),category='tool')
    original=checkpoints.persist
    def revoke(request_id,event,release_check):
        if event['phase']=='prepared':
            with core.connect() as db:db.execute("UPDATE agent_registry_v5 SET status='revoked' WHERE id=?",(agent,))
        return original(request_id,event,release_check)
    monkeypatch.setattr(checkpoints,'persist',revoke)
    result=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert result.status_code==200,result.text
    assert result.json()['status']=='cancelled' and result.json()['known_tool_requests']==1
    saved=client.get(url+'/checkpoints').json()
    assert saved['known_request_count']==1 and saved['actual_total_request_count'] is None
    record=saved['checkpoints'][0]
    assert not record['release_authorized'] and record['response_delivery']=='withheld'
    assert client.get(url+'/evidence').json()['observation']['tool_checkpoints']==saved['checkpoints']
