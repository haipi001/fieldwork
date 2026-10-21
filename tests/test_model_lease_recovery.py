import pytest
import final_core as core
import v5_orchestration as orchestration
import v5_runtime as runtime
from v5_runtime_calls import reserve_call,start_call,fail_call
from tests.test_final import client
from tests.test_v5_runtime_calls import setup as call_setup,task_route


@pytest.mark.parametrize('state',['reserved','calling','unknown','settled'])
def test_expired_lease_with_possible_model_effects_pauses_without_replay(client,state):
    campaign,profile=call_setup(client)
    task,decision=task_route(client,campaign,profile,'recovery-'+state)
    call=reserve_call(decision['id'],task['id'],'call-runner',task['attempt'])
    if state!='reserved':start_call(call['id'])
    if state=='unknown':fail_call(call['id'])
    if state=='settled':
        runtime.record_usage(runtime.UsageReport(decision_id=decision['id'],call_id=call['id'],
            idempotency_key='recovery-settled',input_tokens=10,output_tokens=5,cost_micros=0,runtime_ms=5))
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(task['id'],))
        db.execute('UPDATE agent_tasks SET result_json=? WHERE id=?',(core.dump({'checkpoint':'preserve saved result'}),task['id']))
        before_call=dict(db.execute('SELECT * FROM runtime_calls WHERE id=?',(call['id'],)).fetchone())
        before_usage=[tuple(row) for row in db.execute('SELECT * FROM runtime_usage')]
    result=orchestration.recover_expired_leases()
    assert result=={'requeued':0,'failed':0,'paused':1}
    with core.connect() as db:
        saved=db.execute('SELECT * FROM agent_tasks WHERE id=?',(task['id'],)).fetchone()
        assert saved['status']=='paused' and saved['attempt']==task['attempt']
        assert not saved['lease_owner'] and not saved['lease_expires_at'] and not saved['heartbeat_at']
        assert core.load(saved['error_json'])=={'code':'model_call_interrupted_review','call_states':[state],'automatic_retry':False}
        assert core.load(saved['result_json'])=={'checkpoint':'preserve saved result'}
        assert dict(db.execute('SELECT * FROM runtime_calls WHERE id=?',(call['id'],)).fetchone())==before_call
        assert [tuple(row) for row in db.execute('SELECT * FROM runtime_usage')]==before_usage
        count=db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0]
    assert orchestration.recover_expired_leases()=={'requeued':0,'failed':0}
    assert client.post('/api/v1/orchestration/lease',json={'runner_id':'call-runner','lease_seconds':300}).json()['task'] is None
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0]==count
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==1


@pytest.mark.parametrize('dispatched,expected',[(False,'queued'),(True,'paused')])
def test_expired_call_deadline_releases_only_unsent_reservation(client,monkeypatch,dispatched,expected):
    campaign,profile=call_setup(client)
    task,decision=task_route(client,campaign,profile,'expired-deadline')
    call=reserve_call(decision['id'],task['id'],'call-runner',task['attempt'])
    if dispatched:start_call(call['id'])
    from datetime import datetime,timedelta,timezone
    monkeypatch.setattr(runtime,'_now',lambda:(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(task['id'],))
    recovered=orchestration.recover_expired_leases()
    assert recovered['requeued']==int(not dispatched)
    with core.connect() as db:
        assert db.execute('SELECT status FROM agent_tasks WHERE id=?',(task['id'],)).fetchone()[0]==expected
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?',(call['id'],)).fetchone()[0]==('unknown' if dispatched else 'released')
        assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0]==0


def test_late_team_completion_cannot_erase_interrupted_review_or_publish_draft(client,monkeypatch):
    import v5_research_worker as worker
    from tests.test_v5_research_worker import team,create,wait_group
    worker._stopping.clear()
    _,body=team(client,count=1)
    with core.connect() as db:
        now=core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('lease-fixture','llama_cpp','Lease fixture','http://127.0.0.1:1','fixture',None,1,'{"location":"local"}','healthy',now,now,now))
    group=create(client,body)
    def interrupted(_provider,task,_nodes):
        with core.connect() as db:
            db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(task['id'],))
        assert orchestration.recover_expired_leases()['paused']==1
        return {'summary':'Late response must not become a draft','hypotheses':[],'open_questions':[]},5,5,1
    monkeypatch.setattr(worker.w,'_local_model_output',interrupted)
    assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code==202
    wait_group(group)
    value=client.get(f'/api/v1/workers/research/groups/{group}/execution').json()
    assert value['interrupted_model_tasks']==1 and value['task_counts']=={'paused':1}
    with core.connect() as db:
        task=db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone()
        assert core.load(task['error_json'])['code']=='model_call_interrupted_review' and task['attempt']==1
        assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE source_type='team_research'").fetchone()[0]==0
        # A late known response still settles actual consumption; only draft publication is rejected.
        assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0]==1
