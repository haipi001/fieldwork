import pytest
import final_core as core
import v5_runtime as runtime
import v5_research_worker as worker
import research_resume as resume
from tests.test_final import client
from tests.test_v5_research_worker import team,create,wait_group


def configured(client,count=2,endpoint='http://127.0.0.1:1'):
    worker._stopping.clear()
    _,body=team(client,count=count)
    with core.connect() as db:
        now=core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('resume-model','llama_cpp','Resume fixture',endpoint,'fixture',None,1,'{"location":"local"}','healthy',now,now,now))
    profile=runtime.create_profile(runtime.ProfileCreate(name='Frozen resume fixture',mode='local',config=runtime.ProfileConfig(local_provider_ids=['resume-model'],max_concurrent_calls=1)))
    body['runtime_profile_id']=profile['id'];body['max_concurrency']=1
    group=create(client,body)
    return group,'/api/v1/workers/research/groups/'+group


def approved(client,base):
    plan=client.get(base+'/resume-plan');assert plan.status_code==200,plan.text
    saved=client.post(base+'/resume-policy',json={'confirmed':True,'preview_hash':plan.json()['preview_hash']})
    assert saved.status_code==200,saved.text
    return saved.json()


def output(_provider,_task,_nodes):return {'summary':'Bounded protocol fixture result','hypotheses':[],'open_questions':[]},5,5,1


def test_explicit_policy_dispatches_only_unstarted_tasks_and_survives_repeated_ticks(client,monkeypatch):
    group,base=configured(client)
    before=client.get(base+'/resume-policy').json();assert before['state']=='disabled'
    plan=client.get(base+'/resume-plan');assert plan.status_code==200,plan.text
    assert plan.json()['task_count']==2
    assert client.post(base+'/resume-policy',json={'confirmed':False,'preview_hash':plan.json()['preview_hash']}).status_code==422
    approved(client,base)
    hits=[]
    def model(*args):hits.append(args[1]['id']);return output(*args)
    monkeypatch.setattr(worker.w,'_local_model_output',model)
    result=resume.tick();assert result==[{'group_id':group,'state':'started'}],result
    wait_group(group)
    assert len(hits)==len(set(hits))==2
    assert resume.tick()==[{'group_id':group,'state':'completed'}]
    assert resume.tick()==[]
    with core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0


@pytest.mark.parametrize('change',['budget','source','task','provider','paused'])
def test_stale_policy_blocks_without_model_calls(client,change):
    group,base=configured(client);approved(client,base)
    with core.connect() as db:
        if change=='budget':db.execute("UPDATE research_groups SET budget_json='{}' WHERE id=?",(group,))
        if change=='source':db.execute("UPDATE research_nodes SET body='changed material'")
        if change=='task':db.execute("UPDATE agent_tasks SET objective='changed task' WHERE group_id=?",(group,))
        if change=='provider':db.execute("UPDATE runtime_providers SET model='changed' WHERE id='resume-model'")
        if change=='paused':db.execute("UPDATE agent_tasks SET status='paused' WHERE group_id=?",(group,))
    assert resume.tick()==[{'group_id':group,'state':'blocked'}]
    assert client.get(base+'/resume-policy').json()['state']=='blocked'
    assert resume.tick()==[]
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0


def test_disable_is_cas_and_stops_new_claims_without_mutating_tasks(client):
    group,base=configured(client);policy=approved(client,base)
    assert client.post(base+'/resume-policy/disable',json={'revision':'0'*32}).status_code==409
    assert client.post(base+'/resume-policy/disable',json={'revision':policy['revision']}).status_code==200
    assert resume.tick()==[]
    with core.connect() as db:assert {row[0] for row in db.execute('SELECT status FROM agent_tasks WHERE group_id=?',(group,))}=={'queued'}


def test_real_asgi_restart_dispatches_persisted_policy_without_manual_start_or_replay(client):
    import json,os,secrets,socket,subprocess,sys,threading,time,urllib.error
    from pathlib import Path
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from tests.test_asgi_cold_start import SERVER,request
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));hits.append(self.path)
            raw=json.dumps({'choices':[{'message':{'content':json.dumps({'summary':'Actual local protocol response','hypotheses':[],'open_questions':[]})}}],'usage':{'prompt_tokens':5,'completion_tokens':5}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    model=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=model.serve_forever,daemon=True);thread.start()
    try:
        group,base=configured(client,endpoint=f'http://127.0.0.1:{model.server_port}')
        approved(client,base)
        for restart in range(2):
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
            port=listener.getsockname()[1];token=secrets.token_urlsafe(48)
            config={'token':token,'port':port,'instance':secrets.token_hex(16),'data':str(core.LOCAL_DATA_ROOT),'db':str(core.DB),'fd':listener.fileno()}
            server=SERVER.replace("os.environ.pop('FIELDWORK_DISABLE_AGENT_MONITOR', None)","os.environ['FIELDWORK_DISABLE_AGENT_MONITOR']='1'").replace("os.environ.pop('FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER', None)","os.environ['FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER']='1'")
            process=subprocess.Popen([sys.executable,'-c',server],cwd=Path(__file__).resolve().parents[1]/'backend',stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,pass_fds=(listener.fileno(),),env={**os.environ,'FIELDWORK_DISABLE_RESEARCH_RESUME':'0'})
            try:
                process.stdin.write(json.dumps(config)+'\n');process.stdin.close()
                deadline=time.monotonic()+25
                while True:
                    assert process.poll() is None,process.stderr.read()
                    try:
                        code,_,raw=request(f'http://127.0.0.1:{port}',base+'/resume-policy',token)
                        if code==200 and json.loads(raw)['state']=='completed' and json.loads(raw)['scheduler']['state']=='running':break
                    except (OSError,urllib.error.URLError):pass
                    assert time.monotonic()<deadline,'Persisted resume scheduler did not complete'
                    time.sleep(.05)
                assert len(hits)==2
                with core.connect() as db:
                    assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status='succeeded' AND attempt=1",(group,)).fetchone()[0]==2
                    assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0]==2
                    assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0
            finally:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                listener.close()
            diagnostics=process.stderr.read()
            assert process.returncode in (0,-15),diagnostics
            assert 'ERROR:' not in diagnostics
            with core.connect() as db:assert core.load(db.execute("SELECT value FROM app_metadata WHERE key='research_resume_scheduler'").fetchone()[0])['state']=='stopped'
            process.stdout.close();process.stderr.close()
    finally:
        model.shutdown();model.server_close();thread.join(timeout=2)


def test_approval_does_not_expand_to_already_attempted_queued_task(client,monkeypatch):
    group,base=configured(client)
    with core.connect() as db:
        old=db.execute('SELECT id FROM agent_tasks WHERE group_id=? ORDER BY id LIMIT 1',(group,)).fetchone()[0]
        db.execute('UPDATE agent_tasks SET attempt=1,priority=1000 WHERE id=?',(old,))
    plan=client.get(base+'/resume-plan').json();assert plan['task_count']==1 and old not in plan['task_hashes']
    approved(client,base);hits=[]
    def model(*args):hits.append(args[1]['id']);return output(*args)
    monkeypatch.setattr(worker.w,'_local_model_output',model)
    assert resume.tick()[0]['state']=='started';wait_group(group)
    assert len(hits)==1 and old not in hits
    assert resume.tick()[0]['state']=='completed'
    with core.connect() as db:assert tuple(db.execute('SELECT status,attempt FROM agent_tasks WHERE id=?',(old,)).fetchone())==('queued',1)


def test_disable_during_inflight_call_stops_next_approved_claim(client,monkeypatch):
    import threading
    group,base=configured(client);policy=approved(client,base)
    started=threading.Event();release=threading.Event();hits=[]
    def model(*args):
        hits.append(args[1]['id']);started.set();assert release.wait(5)
        return output(*args)
    monkeypatch.setattr(worker.w,'_local_model_output',model)
    assert resume.tick()[0]['state']=='started'
    try:
        assert started.wait(3)
        assert client.post(base+'/resume-policy/disable',json={'revision':policy['revision']}).status_code==200
    finally:release.set()
    wait_group(group)
    assert len(hits)==1 and resume.tick()==[]
    with core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt=0",(group,)).fetchone()[0]==1


def test_rejected_model_output_stops_policy_without_automatic_retry(client,monkeypatch):
    group,base=configured(client);approved(client,base);hits=[]
    def invalid(*args):hits.append(args[1]['id']);return {'summary':'bad protocol output','unexpected':True},5,5,1
    monkeypatch.setattr(worker.w,'_local_model_output',invalid)
    assert resume.tick()[0]['state']=='started';wait_group(group)
    assert len(hits)==1
    assert resume.tick()[0]['state']=='blocked'
    assert resume.tick()==[]
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==1


def test_policy_preview_cas_and_admission_preserve_explicit_permission(client,monkeypatch):
    group,base=configured(client)
    plan=client.get(base+'/resume-plan').json();approved(client,base)
    assert client.post(base+'/resume-policy',json={'confirmed':True,'preview_hash':plan['preview_hash']}).status_code==409
    admission=client.get('/api/v1/workers/research/admission').json()
    assert client.patch('/api/v1/workers/research/admission',json={'state':'draining','expected_updated_at':admission['updated_at']}).status_code==200
    assert resume.tick()==[{'group_id':group,'state':'waiting_admission'}]
    assert client.get(base+'/resume-policy').json()['state']=='enabled'
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0


def test_shutdown_joins_exact_tick_even_when_that_tick_raises(client,monkeypatch):
    import asyncio,threading
    import app
    entered=threading.Event();release=threading.Event();events=[]
    def failing_pass():
        entered.set();assert release.wait(5)
        events.append('tick_finished');raise ValueError('fixed fixture failure')
    monkeypatch.setattr(resume,'scheduler_pass',failing_pass)
    monkeypatch.setattr(resume,'mark_scheduler',lambda state,*_:events.append(state))
    async def exercise():
        task=asyncio.create_task(app.research_resume_loop(core.LOCAL_DATA_ROOT.resolve()))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel();await asyncio.sleep(.05)
            assert not task.done() and events==[]
        finally:release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(exercise())
    assert events==['tick_finished','stopped']


def test_ninth_enabled_group_is_checked_even_when_first_eight_are_running(client,monkeypatch):
    from tests.test_v5_team_plan import _body
    group,base=configured(client,count=1);approved(client,base);groups=[group]
    with core.connect() as db:original=dict(db.execute('SELECT * FROM research_groups WHERE id=?',(group,)).fetchone())
    for index in range(8):
        body=_body({'id':original['campaign_id']},original['run_id'],1)
        body['runtime_profile_id']=original['runtime_profile_id']
        proposal=client.post('/api/v1/orchestration/groups/team/preview',json=body).json()
        saved=client.post('/api/v1/orchestration/groups/team',json={**body,'preview_hash':proposal['preview_hash'],'idempotency_key':f'rotation-group-{index}'})
        assert saved.status_code==201,saved.text
        added=saved.json()['group']['id'];groups.append(added)
        approved(client,'/api/v1/workers/research/groups/'+added)
    ordered=sorted(groups)
    class Active:
        def is_alive(self):return True
    started=[]
    def start(group_id,**_):started.append(group_id);return {'status':'fixture_start_observed'}
    with monkeypatch.context() as patch:
        patch.setattr(worker,'_jobs',{key:Active() for key in ordered[:8]})
        patch.setattr(worker,'_start_group_research',start)
        first=resume.tick();second=resume.tick()
    assert len(first)==len(second)==8
    assert {row['group_id'] for row in first+second}==set(groups)
    assert started==[ordered[8]]
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0


def test_approved_task_requeued_after_attempt_requires_review_not_completion(client):
    group,base=configured(client,count=1);approved(client,base)
    with core.connect() as db:db.execute("UPDATE agent_tasks SET status='queued',attempt=1 WHERE group_id=?",(group,))
    before=client.get(base+'/resume-policy').json()
    assert before['task_progress']=={'unstarted':0,'running':0,'drafts_saved':0,'review_required':1,'missing':0}
    assert resume.tick()==[{'group_id':group,'state':'blocked'}]
    assert client.get(base+'/resume-policy').json()['state']=='blocked'
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0


def test_saved_policy_completion_is_not_trusted_over_current_task_records(client):
    group,base=configured(client,count=1);approved(client,base)
    with core.connect() as db:
        value=resume.read(db,group);value['state']='completed';resume.write(db,group,value)
        original=db.execute('SELECT value FROM app_metadata WHERE key=?',(resume.PREFIX+group,)).fetchone()[0]
    observed=client.get(base+'/resume-policy').json()
    assert observed['state']=='blocked' and observed['stored_state']=='completed'
    assert observed['task_progress']['unstarted']==1
    with core.connect() as db:assert db.execute('SELECT value FROM app_metadata WHERE key=?',(resume.PREFIX+group,)).fetchone()[0]==original


def test_policy_review_tasks_are_exact_bounded_redacted_and_read_only(client):
    group,base=configured(client,count=8);approved(client,base)
    with core.connect() as db:
        ids=[row[0] for row in db.execute('SELECT id FROM agent_tasks WHERE group_id=? ORDER BY id',(group,))]
        secret_error={'code':'model_output_invalid_usage_settled','model_output_reason':'invalid_json','model_finish_reason':'stop','raw':'PRIVATE_MARKER_MUST_NOT_LEAK'}
        db.execute("UPDATE agent_tasks SET status='paused',error_json=? WHERE group_id=?",(core.dump(secret_error),group))
        before=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
    response=client.get(base+'/resume-policy');assert response.status_code==200,response.text
    value=response.json();review=value['review_tasks']
    assert value['task_progress']['review_required']==8
    assert [row['id'] for row in review['items']]==ids[:5] and review['remaining']==3
    assert all(row['error_code']=='model_output_invalid_usage_settled' and row['model_output_reason']=='invalid_json' and not row['automatic_retry'] for row in review['items'])
    assert 'PRIVATE_MARKER' not in response.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==before
        assert db.execute("SELECT count(*) FROM agent_tasks WHERE group_id=? AND status='paused'",(group,)).fetchone()[0]==8
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET error_json=? WHERE id=?",(core.dump({'code':'PRIVATE_CODE','model_output_reason':'PRIVATE_REASON','model_finish_reason':'PRIVATE_FINISH'}),ids[0]))
    first=client.get(base+'/resume-policy').json()['review_tasks']['items'][0]
    assert first['error_code'] is first['model_output_reason'] is first['model_finish_reason'] is None



def test_review_summary_tolerates_malformed_legacy_result_and_error_types(client):
    group,base=configured(client,count=1);approved(client,base)
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET status='succeeded',result_json='[]',error_json=? WHERE group_id=?",(core.dump({'code':[],'model_output_reason':{},'model_finish_reason':True}),group))
    response=client.get(base+'/resume-policy');assert response.status_code==200,response.text
    value=response.json();assert value['task_progress']['review_required']==1
    assert value['review_tasks']['items'][0]['error_code'] is None
