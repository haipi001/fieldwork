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


@pytest.mark.parametrize('url,method,status,body',[('https://evil.test/me','GET',200,b'{"id":1}'),('https://example.test/me?token=x','GET',200,b'{"id":1}'),('https://example.test/me','POST',200,b'{"id":1}'),('https://example.test/me','GET',401,b'{"id":1}'),('https://example.test/me','GET',200,b'x'*65537)])
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
