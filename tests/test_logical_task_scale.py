"""Real queue APIs at logical-task scale; no tools or model dispatch."""
import json,time
from concurrent.futures import ThreadPoolExecutor
import pytest
import final_core as core
from tests.test_final import client
from tests.test_v5_team_plan import _setup,_body


@pytest.mark.parametrize('count',[100,1000])
def test_logical_tasks_parallel_leasing_and_owner_loss_recovery(client,count):
    _,campaign,run=_setup(client)
    started=time.monotonic()
    for index in range(count//20):
        body={**_body(campaign,run,20),'max_concurrency':8}
        preview=client.post('/api/v1/orchestration/groups/team/preview',json=body)
        assert preview.status_code==200,preview.text
        committed=client.post('/api/v1/orchestration/groups/team',json={**body,'preview_hash':preview.json()['preview_hash'],'idempotency_key':f'scale-{count}-{index}'})
        assert committed.status_code==201,committed.text
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0]==count
        grants=set()
        for row in db.execute('SELECT tool_grants_json FROM agent_tasks'):grants.update(json.loads(row[0]))
    response=client.put('/api/v1/runners/scale-runner',json={'id':'scale-runner','name':'Isolated logical-scale fixture','kind':'research-worker','capabilities':sorted(grants),'labels':{},'max_concurrency':32})
    assert response.status_code==200,response.text
    def claim(_):
        response=client.post('/api/v1/orchestration/lease',json={'runner_id':'scale-runner','lease_seconds':60})
        assert response.status_code==200,response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=16) as workers:responses=list(workers.map(claim,range(64)))
    leased=[row['task']['id'] for row in responses if row.get('task')]
    assert len(leased)==32 and len(set(leased))==32
    assert all(row.get('task') or row.get('reason')=='runner_at_capacity' for row in responses)
    with core.connect() as db:
        assert db.execute("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM agent_tasks WHERE status='running' GROUP BY group_id)").fetchone()[0]<=8
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE lease_owner='scale-runner'")
    recovered=client.post('/api/v1/orchestration/recover').json()
    assert recovered=={'requeued':32,'failed':0}
    assert client.get('/api/v1/runners/scale-runner').json()['active_jobs']==0
    stale=client.post('/api/v1/orchestration/tasks/'+leased[0]+'/complete',json={
        'runner_id':'scale-runner','outcome':'succeeded','result':{'kind':'stale_owner_fixture'},
        'usage':{'input_tokens':0,'output_tokens':0,'cost_micros':0,'runtime_ms':0}})
    assert stale.status_code==409,stale.text
    with ThreadPoolExecutor(max_workers=16) as workers:responses=list(workers.map(claim,range(64)))
    reclaimed=[row['task']['id'] for row in responses if row.get('task')]
    assert len(reclaimed)==32 and len(set(reclaimed))==32
    with core.connect() as db:
        retry=db.execute("SELECT id,attempt FROM agent_tasks WHERE status='running' AND attempt>1 LIMIT 1").fetchone()
        assert retry is not None
    before=core.DB.read_bytes()
    for endpoint,body in [('heartbeat',{}),('checkpoints',{'checkpoint':{'kind':'old_attempt'}}),('complete',{'outcome':'succeeded','result':{'kind':'old_attempt'}})]:
        for attempt,status in [(retry['attempt']-1,409),(None,409),(True,422),(str(retry['attempt']),422)]:
            result=client.post('/api/v1/orchestration/tasks/'+retry['id']+'/'+endpoint,json={'runner_id':'scale-runner','lease_attempt':attempt,**body})
            assert result.status_code==status,result.text
    assert core.DB.read_bytes()==before
    assert client.post('/api/v1/orchestration/tasks/'+retry['id']+'/heartbeat',json={'runner_id':'scale-runner','lease_attempt':retry['attempt']}).status_code==200

    seen=set();cursor=None
    while True:
        params={'campaign_id':campaign['id'],'limit':100}
        if cursor:params['cursor']=cursor
        response=client.get('/api/v1/orchestration/tasks',params=params)
        assert response.status_code==200,response.text
        page=response.json();ids={item['id'] for item in page['items']}
        assert not seen&ids;seen.update(ids)
        cursor=page['page']['next_cursor']
        if not cursor:break
    assert len(seen)==count
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0]==count
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM capability_uses_v6').fetchone()[0]==0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print(f'logical_scale={count}; active_limit=32; group_limit=8; recovered=32; elapsed_seconds={time.monotonic()-started:.3f}; model_calls=0')
