import json
import pytest
import final_core as core
import v5_research_worker as worker
from tests.test_final import client
from tests.test_v5_research_worker import team,create


def marker(group,runner,state='running',stamp='2000-01-01T00:00:00+00:00'):
    with core.connect() as db:
        db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
            ('research-execution:'+group,json.dumps({'state':state,'runner_id':runner}),stamp))


def test_heartbeat_refresh_and_foreign_owner_fences_claims_without_mutation(client):
    worker._stopping.clear()
    _,body=team(client,count=1);group=create(client,body)
    marker(group,worker.RUNNER_ID)
    assert worker._heartbeat_execution(group)
    url=f'/api/v1/workers/research/groups/{group}/execution'
    value=client.get(url).json()
    assert value['state']=='active_marker' and not value['automatic_restart'] and not value['sends_model_requests']
    assert value['task_counts']=={'queued':1} and value['call_counts']=={} and not value['marker_is_execution_proof']
    marker(group,'another-process')
    with core.connect() as db:
        before=[tuple(x) for x in db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,))]
    assert not worker._heartbeat_execution(group)
    assert worker._claim(group) is None
    assert client.get(url).json()['state']=='stale_marker'
    with core.connect() as db:
        assert before==[tuple(x) for x in db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,))]
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        assert core.load(db.execute('SELECT value FROM app_metadata WHERE key=?',('research-execution:'+group,)).fetchone()[0])['runner_id']=='another-process'


def test_dispatcher_losing_ownership_stops_without_claiming_or_clobbering_new_owner(client,monkeypatch):
    _,body=team(client,count=1);group=create(client,body)
    marker(group,'new-owner')
    monkeypatch.setattr(worker,'_claim',lambda *_:pytest.fail('foreign dispatcher must not claim'))
    worker._run_group(group,1)
    with core.connect() as db:
        assert core.load(db.execute('SELECT value FROM app_metadata WHERE key=?',('research-execution:'+group,)).fetchone()[0])=={'state':'running','runner_id':'new-owner'}
    assert client.get(f'/api/v1/workers/research/groups/{group}/execution').json()['task_counts']=={'queued':1}


def test_execution_read_never_creates_marker_or_recovers_tasks(client):
    _,body=team(client,count=1);group=create(client,body)
    response=client.get(f'/api/v1/workers/research/groups/{group}/execution')
    assert response.status_code==200 and response.json()['state']=='not_started'
    assert response.json()['last_heartbeat_at'] is None
    with core.connect() as db:assert not db.execute('SELECT 1 FROM app_metadata WHERE key=?',('research-execution:'+group,)).fetchone()
    assert client.get('/api/v1/workers/research/groups/missing/execution').status_code==404


def test_stale_marker_with_unexpired_lease_cannot_start_second_dispatcher(client,monkeypatch):
    _,body=team(client,count=2);group=create(client,body)
    marker(group,'old-owner')
    with core.connect() as db:
        task=db.execute('SELECT id FROM agent_tasks WHERE group_id=? ORDER BY id LIMIT 1',(group,)).fetchone()[0]
        db.execute("UPDATE agent_tasks SET status='running',lease_owner='old-owner',lease_expires_at='2999-01-01T00:00:00+00:00' WHERE id=?",(task,))
    monkeypatch.setattr(worker,'_route',lambda *_:{})
    monkeypatch.setattr(worker,'_provider',lambda *_:None)
    result=client.post(f'/api/v1/workers/research/groups/{group}/start')
    assert result.status_code==409,result.text
    status=client.get(f'/api/v1/workers/research/groups/{group}/execution').json()
    assert status['state']=='stale_marker' and status['unexpired_leases']==1
    assert status['marker_runner_id']=='old-owner' and status['call_counts']=={}


def test_owner_change_during_inflight_work_prevents_next_queued_claim(client,monkeypatch):
    worker._stopping.clear()
    _,body=team(client,count=2);group=create(client,body)
    marker(group,worker.RUNNER_ID)
    claims=[]
    original=worker._claim
    def claim(group_id):
        task=original(group_id)
        if task:claims.append(task['id'])
        return task
    def execute(_task):
        marker(group,'replacement-owner')
        return {'status':'fixture_done'}
    with core.connect() as db:
        budget=core.load(db.execute('SELECT budget_json FROM research_groups WHERE id=?',(group,)).fetchone()[0])
        budget['max_concurrency']=1
        db.execute('UPDATE research_groups SET budget_json=? WHERE id=?',(core.dump(budget),group))
    monkeypatch.setattr(worker,'_claim',claim);monkeypatch.setattr(worker,'_execute',execute)
    worker._run_group(group,1)
    assert len(claims)==1
    value=client.get(f'/api/v1/workers/research/groups/{group}/execution').json()
    assert value['marker_runner_id']=='replacement-owner' and value['task_counts']=={'queued':1,'running':1} and value['call_counts']=={}


def test_other_real_python_process_cannot_heartbeat_or_claim_owned_group(client):
    import subprocess,sys
    from pathlib import Path
    _,body=team(client,count=1);group=create(client,body)
    marker(group,worker.RUNNER_ID,stamp=core.utcnow())
    script="""import json,sys
from pathlib import Path
import final_core as core
import v5_research_worker as worker
core.DB=Path(sys.argv[1]);core.LOCAL_DATA_ROOT=Path(sys.argv[2])
print(json.dumps({'different_runner':worker.RUNNER_ID!=sys.argv[4],
 'heartbeat':worker._heartbeat_execution(sys.argv[3]),'claimed':worker._claim(sys.argv[3]) is not None}))
"""
    result=subprocess.run([sys.executable,'-c',script,str(core.DB),str(core.LOCAL_DATA_ROOT),group,worker.RUNNER_ID],
        cwd=Path(__file__).resolve().parents[1]/'backend',capture_output=True,text=True,timeout=10,check=True)
    assert json.loads(result.stdout)=={'different_runner':True,'heartbeat':False,'claimed':False}
    status=client.get(f'/api/v1/workers/research/groups/{group}/execution').json()
    assert status['marker_runner_id']==worker.RUNNER_ID and status['task_counts']=={'queued':1} and status['call_counts']=={}


def test_dispatcher_refreshes_heartbeat_while_waiting_for_actual_inflight_thread(client,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    worker._stopping.clear()
    _,body=team(client,count=1);group=create(client,body)
    marker(group,worker.RUNNER_ID)
    started=threading.Event();release=threading.Event()
    def execute(task):
        started.set()
        assert release.wait(5)
        with core.connect() as db:
            db.execute("UPDATE agent_tasks SET status='succeeded',lease_owner=NULL,lease_expires_at=NULL WHERE id=?",(task['id'],))
    monkeypatch.setattr(worker,'_execute',execute)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(worker._run_group,group,1)
        try:
            assert started.wait(3)
            with core.connect() as db:
                db.execute("UPDATE app_metadata SET updated_at='2000-01-01T00:00:00+00:00' WHERE key=?",('research-execution:'+group,))
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                status=client.get(f'/api/v1/workers/research/groups/{group}/execution').json()
                if status['state']=='active_marker':break
                time.sleep(.02)
            assert status['state']=='active_marker' and status['task_counts']=={'running':1}
            assert status['call_counts']=={}
        finally:release.set()
        future.result(timeout=5)
    assert client.get(f'/api/v1/workers/research/groups/{group}/execution').json()['state']=='stopped'
