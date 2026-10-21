import json
import pytest
import guided_capture as capture
import guided_http
import final_core as core
from tests.test_final import client
from tests.test_guided_research import candidate


def test_projection_keeps_actual_binding_fields_not_credentials():
    body=json.dumps({'id':'a','owner_id':'a','email':'private@example.test','token':'secret','data':{'owner':{'id':'b'},'password':'hidden'},'extra':{'id':'discard'}}).encode()
    result=capture.project_response('https://example.test/api/me','https://example.test','GET',200,body)
    assert json.loads(result['preview'])=={'id':'a','owner_id':'a','data':{'owner':{'id':'b'}}}
    assert 'secret' not in result['preview'] and 'email' not in result['preview']


@pytest.mark.parametrize('url,method,status,body',[('https://evil.test/me','GET',200,b'{"id":1}'),('https://example.test/me?token=x','GET',200,b'{"id":1}'),('https://example.test/me','POST',200,b'{"id":1}'),('https://example.test/me','GET',500,b'{"id":1}'),('https://example.test/me','GET',200,b'x'*65537)])
def test_projection_rejects_unusable_sources(url,method,status,body):
    assert capture.project_response(url,'https://example.test',method,status,body) is None


def test_login_projections_form_unique_plan_without_manual_fields(client):
    run,c=candidate(client)
    project=core.get_run(run['id'])['engagement_id']
    with core.connect() as db:
        scope=core.get_engagement(project)['current_scope_snapshot_id']
        row=db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(scope,)).fetchone()
        rules=core.load(row['rules'],{});rules['allow_authentication']=True
        db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',(core.dump(rules),scope))
    accounts=[]
    for label in ['a','b']:
        accounts.append(client.post(f'/api/v1/engagements/{project}/identities',json={'label':label,'role':'user','session_status':'ready'}).json())
    def response(url,value):return capture.project_response(url,'https://example.test','GET',200,json.dumps(value).encode())
    owner=[response('https://example.test/api/me',{'id':'a','token':'discard'}),response(c['target'],{'owner_id':'a','secret':'discard'})]
    assert capture.store_responses(accounts[0]['id'],run['id'],owner)==2
    assert capture.store_responses(accounts[0]['id'],run['id'],owner)==0
    assert capture.store_responses(accounts[1]['id'],run['id'],[response('https://example.test/api/me',{'id':'b'})])==1
    with core.connect() as db:exchanges=[dict(x) for x in db.execute('SELECT * FROM http_exchanges WHERE run_id=?',(run['id'],))]
    plan=guided_http.plan({**c,'mode':'traditional'},exchanges,accounts)
    assert plan and plan['principal_field']=='id' and plan['owner_field']=='owner_id'
    assert len(plan['sources'])==3
    other,_=candidate(client)
    assert capture.store_responses(accounts[0]['id'],other['id'],owner)==0


def test_complete_login_imports_material_before_resume(client,monkeypatch):
    import session_capture
    import guided_research
    from tests.test_guided_identities import authorized
    project=authorized(client)
    identity=client.post(f"/api/v1/engagements/{project['id']}/quick-identities").json()['identities'][0]
    run_id=core.uid('run')
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'completed','report',0,None,None,None,None,core.utcnow()))
    response=capture.project_response('https://accounts.test/api/me','https://accounts.test','GET',200,b'{"id":"account-a","token":"discard"}')
    monkeypatch.setattr(session_capture,'status',lambda _: {'identity_id':identity['id'],'run_id':run_id})
    monkeypatch.setattr(session_capture,'complete',lambda _: {'headers':{'Cookie':'session=test-only'},'cookie_count':1,'domain_count':1,'requests_seen':1,'requests_blocked':0,'responses':[response]})
    monkeypatch.setattr(session_capture,'store_keychain',lambda *args:'keychain://fieldwork-session/test-fixture')
    def resumed(identity_id):
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM http_exchanges WHERE run_id=?',(run_id,)).fetchone()[0]==1
        return []
    monkeypatch.setattr(guided_research,'resume_after_identity',resumed)
    result=client.post('/api/v1/session-captures/fixture/complete')
    assert result.status_code==200 and result.json()['imported_responses']==1
    assert result.json()['resume_status']=='checked'
    assert 'test-only' not in result.text and 'discard' not in result.text


def test_registered_projection_is_private_and_detects_changed_exchange(client):
    from pathlib import Path
    import stat
    import research_material_review as review
    project=client.post('/api/v1/engagements',json={'name':'Projection source fixture','target':'https://example.test','scope':{'allow_authentication':True,'auth_allowed_hosts':['example.test']},'policy':{}}).json()
    project=client.post(f"/api/v1/engagements/{project['id']}/confirm").json()
    run_id=core.uid('run')
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'completed','report',0,None,None,None,None,core.utcnow()))
    identity=client.post(f"/api/v1/engagements/{project['id']}/identities",json={'label':'Projection identity','role':'user','session_status':'ready'}).json()
    body=b'{"id":"private-user@example.test","token":"private-cookie-value"}'
    response=capture.project_response('https://example.test/api/me','https://example.test','GET',200,body)
    assert capture.store_responses(identity['id'],run_id,[response])==1
    assert capture.store_responses(identity['id'],run_id,[response])==0
    with core.connect() as db:
        artifact=db.execute("SELECT * FROM artifacts WHERE run_id=? AND kind='http.ownership_projection'",(run_id,)).fetchone()
        raw=Path(artifact['uri']).read_bytes()
        assert b'private-user@example.test' not in raw and b'private-cookie-value' not in raw
        assert stat.S_IMODE(Path(artifact['uri']).stat().st_mode)==0o600
        value=json.loads(raw)
        assert capture.projection_current(db,run_id,value)
        assert value['ownership_fingerprints']['id']==__import__('hashlib').sha256(b'private-user@example.test').hexdigest()
        assert db.execute("SELECT subject FROM observations WHERE raw_ref=?",(artifact['id'],)).fetchone()[0]==response['url']
    assert client.post(f'/api/v1/runs/{run_id}/correlate').json()['count']==0
    url=f"/api/v1/research/engagements/{project['id']}/runs/{run_id}/campaign-source-plan"
    source=client.get(url);assert source.status_code==200,source.text
    campaign=client.post(f"/api/v1/engagements/{project['id']}/campaigns",json={'name':'Projection review','objective':'Review current metadata','source_run_id':run_id,'source_plan_hash':source.json()['preview_hash']})
    assert campaign.status_code==201,campaign.text
    with core.connect() as db:
        node=db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND node_type='observation'",(campaign.json()['id'],)).fetchone()
        assert core.load(node['attributes_json'])['sensitivity']=='private'
        def checked():return review.source_status(db,node,run_id,{'remaining':8000000,'checked':{}})
        assert checked()=='verified'
        from tests.test_v5_team_plan import _body
        import v5_research_worker as research
        body=_body(campaign.json(),run_id,1)
        team_preview=client.post('/api/v1/orchestration/groups/team/preview',json=body)
        assert team_preview.status_code==200,team_preview.text
        team=client.post('/api/v1/orchestration/groups/team',json=body|{'preview_hash':team_preview.json()['preview_hash'],'idempotency_key':'ownership-source-preflight'})
        assert team.status_code==201,team.text
        task=db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(team.json()['group']['id'],)).fetchone()
        assert research._inputs(db,task)
        db.execute('UPDATE http_exchanges SET response_body_preview=? WHERE id=?',('{"id":"changed-user"}',value['exchange_id']))
        assert checked()=='changed'
        from fastapi import HTTPException
        with pytest.raises(HTTPException):research._inputs(db,task)
        db.commit()
        assert client.get(url).status_code==409
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0


def test_deeper_legacy_projection_is_preserved_without_inventing_supported_fields(client):
    run,c=candidate(client)
    project=core.get_run(run['id'])['engagement_id']
    with core.connect() as db:
        scope=core.get_engagement(project)['current_scope_snapshot_id']
        row=db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(scope,)).fetchone()
        rules=core.load(row['rules']);rules['allow_authentication']=True
        db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',(core.dump(rules),scope))
    identity=client.post(f'/api/v1/engagements/{project}/identities',json={'label':'Deeper projection','role':'user','session_status':'ready'}).json()
    value=capture.project_response('https://example.test/api/me','https://example.test','GET',200,b'{"data":{"user":{"id":"bounded-user"}}}')
    assert capture.store_responses(identity['id'],run['id'],[value])==1
    with core.connect() as db:
        artifact=db.execute("SELECT uri FROM artifacts WHERE kind='http.ownership_projection'").fetchone()
        from pathlib import Path
        assert json.loads(Path(artifact['uri']).read_text())['ownership_fingerprints']=={}


def test_denial_projection_preserves_status_without_response_content():
    for status in (401,403):
        result=capture.project_response('https://example.test/object','https://example.test','GET',status,b'{"id":"private-id","error":"private detail","token":"private-cookie"}')
        assert result['status']==status and result['preview']=='{}'
        assert 'private' not in json.dumps(result)


def test_denial_registration_projects_first_class_counterevidence(client):
    run,c=candidate(client);project=core.get_run(run['id'])['engagement_id']
    with core.connect() as db:
        db.execute('UPDATE analysis_runs SET synthetic=0 WHERE id=?',(run['id'],))
        scope=core.get_engagement(project)['current_scope_snapshot_id'];row=db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(scope,)).fetchone()
        rules=core.load(row['rules']);rules['allow_authentication']=True
        db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',(core.dump(rules),scope))
    identity=client.post(f'/api/v1/engagements/{project}/identities',json={'label':'Denied identity','role':'user','session_status':'ready'}).json()
    response=capture.project_response(c['target'],'https://example.test','GET',403,b'{"error":"denied","token":"private"}')
    changed_status=capture.project_response(c['target'],'https://example.test','GET',401,b'{"error":"denied","token":"private"}')
    assert capture.store_responses(identity['id'],run['id'],[response,changed_status])==2
    assert capture.store_responses(identity['id'],run['id'],[response,changed_status])==0
    with core.connect() as db:
        evidence=db.execute("SELECT * FROM evidence_v2 WHERE evidence_type='http.ownership_denial'").fetchone()
        assert evidence['polarity']=='counterevidence' and evidence['artifact_id']
        assert db.execute('SELECT response_status FROM http_exchanges').fetchone()[0]==403
    url=f"/api/v1/research/engagements/{project}/runs/{run['id']}/campaign-source-plan"
    preview=client.get(url);assert preview.status_code==200,preview.text
    assert preview.json()['counts']['counterevidence']==2
    created=client.post(f'/api/v1/engagements/{project}/campaigns',json={'name':'Denial source review','objective':'Review observed denial','source_run_id':run['id'],'source_plan_hash':preview.json()['preview_hash']})
    assert created.status_code==201,created.text
    with core.connect() as db:
        row=db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND node_type='counterevidence' AND source_ref=?",(created.json()['id'],evidence['id'])).fetchone()
        assert row['source_ref']==evidence['id'] and core.load(row['attributes_json'])['polarity']=='counterevidence'


def test_projection_registration_rolls_back_file_and_database_together(client):
    import sqlite3
    from pathlib import Path
    import traditional_runtime as http
    run,c=candidate(client);project=core.get_run(run['id'])['engagement_id']
    with core.connect() as db:
        scope=core.get_engagement(project)['current_scope_snapshot_id'];row=db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(scope,)).fetchone()
        rules=core.load(row['rules']);rules['allow_authentication']=True
        db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',(core.dump(rules),scope))
    identity=client.post(f'/api/v1/engagements/{project}/identities',json={'label':'Rollback identity','role':'user','session_status':'ready'}).json()
    before=set(http.ARTIFACT_ROOT.glob('*.json'))
    with core.connect() as db:
        db.execute("CREATE TRIGGER reject_projection BEFORE INSERT ON observations WHEN NEW.observation_type='http.ownership_projection' BEGIN SELECT RAISE(ABORT,'injected registration failure'); END")
    value=capture.project_response('https://example.test/api/me','https://example.test','GET',200,b'{"id":"bounded"}')
    with pytest.raises(sqlite3.IntegrityError):capture.store_responses(identity['id'],run['id'],[value])
    assert set(http.ARTIFACT_ROOT.glob('*.json'))==before
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM http_exchanges').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM observations WHERE observation_type='http.ownership_projection'").fetchone()[0]==0


@pytest.mark.parametrize('status',[200,403])
def test_fresh_recapture_registers_material_when_legacy_exchange_exists(client,status):
    import traditional_runtime as http
    run,c=candidate(client);project=core.get_run(run['id'])['engagement_id']
    with core.connect() as db:
        scope=core.get_engagement(project)['current_scope_snapshot_id']
        rules=core.load(db.execute('SELECT rules FROM scope_snapshots WHERE id=?',(scope,)).fetchone()[0])
        rules['allow_authentication']=True
        db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?',(core.dump(rules),scope))
    identity=client.post(f'/api/v1/engagements/{project}/identities',json={'label':'Recapture identity','role':'user','session_status':'ready'}).json()
    value=capture.project_response('https://example.test/api/me','https://example.test','GET',status,b'{"id":"account-a"}')
    result={'status':status,'headers':{},'body_preview':value['preview'],'body_sha256':value['sha256'],'body_bytes':value['bytes']}
    old=http._record_exchange(core.get_run(run['id']),http.ReplayRequest(url=value['url']),result,identity['id'],'legacy_capture_fixture')
    with core.connect() as db:
        db.execute("UPDATE http_exchanges SET source='guided_login_projection' WHERE id=?",(old['id'],))
        before=dict(db.execute('SELECT * FROM http_exchanges WHERE id=?',(old['id'],)).fetchone())
        assert db.execute("SELECT count(*) FROM artifacts WHERE kind='http.ownership_projection'").fetchone()[0]==0
    assert capture.store_responses(identity['id'],run['id'],[value])==1
    assert capture.store_responses(identity['id'],run['id'],[value])==0
    with core.connect() as db:
        assert dict(db.execute('SELECT * FROM http_exchanges WHERE id=?',(old['id'],)).fetchone())==before
        assert db.execute('SELECT count(*) FROM http_exchanges').fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM artifacts WHERE kind='http.ownership_projection'").fetchone()[0]==1
        expected=1 if status==403 else 0
        assert db.execute("SELECT count(*) FROM evidence_v2 WHERE evidence_type='http.ownership_denial'").fetchone()[0]==expected
