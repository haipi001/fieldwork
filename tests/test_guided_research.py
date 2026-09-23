import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import guided_research as guided
import final_core
from tests.test_final import client, create_ready


def candidate(client, target='https://example.test/object', category='authorization'):
    engagement = create_ready(client)
    run = {'id': final_core.uid('run')}
    with final_core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (run['id'],engagement['id'],'traditional',engagement['current_scope_snapshot_id'],engagement['current_policy_id'],'completed','report',1,None,None,None,None,final_core.utcnow()))
    # Direct record construction stays in the fixture's temporary database.
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed' WHERE id=?", (run['id'],))
        oid=final_core.uid('obs')
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (oid,run['id'],engagement['id'],'traditional','authorization',target,
                    'Recorded object belongs to a different authorized test identity',.7,'fixture',None,final_core.utcnow()))
    value=client.post(f"/api/v1/runs/{run['id']}/candidates",json={'title':'Object read candidate','category':category,'target':target,'hypothesis':'Object owner boundary requires independent verification','observation_ids':[oid]}).json()
    return run, value


def wait(client, job):
    for _ in range(200):
        job=client.get(f"/api/v1/guided-research/{job['id']}").json()
        if job['status'] not in guided.ACTIVE:
            return job
        time.sleep(.01)
    raise AssertionError('worker did not finish')


def test_prepare_persists_provenance_without_promoting(client):
    run,c=candidate(client)
    before=client.get(f"/api/v1/candidates/{c['id']}").json()['candidate']
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    assert job['status']=='awaiting_input'
    item=job['result']['items'][0]
    assert item['sources'][0]['id'] in c['evidence_ids']
    assert item['draft']['observed_behavior']
    assert item['draft']['impact'] is None and item['draft']['severity'] is None
    assert item['verified'] is False
    assert job['result']['requests_sent']==0
    assert job['steps'][3]['status']=='needs_input'
    assert client.get(f"/api/v1/candidates/{c['id']}").json()['candidate']==before
    assert client.get(f"/api/v1/runs/{run['id']}/guided-research").json()['id']==job['id']
    again=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json()
    assert again['id']==job['id'] and again['reused']


def test_updated_evidence_creates_new_revision(client):
    run,c=candidate(client)
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    with final_core.connect() as db:
        db.execute('UPDATE observations SET summary=? WHERE run_id=?', ('Newly collected response evidence', run['id']))
    updated=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    assert updated['id']!=job['id']
    assert updated['result']['items'][0]['draft']['observed_behavior']=='Newly collected response evidence'
    assert client.get(f"/api/v1/guided-research/{job['id']}").json()['result']==job['result']


def test_cross_run_and_archived_candidates_rejected(client):
    run,c=candidate(client)
    other,_=candidate(client)
    assert client.post(f"/api/v1/runs/{other['id']}/guided-research",json={'candidate_id':c['id']}).status_code==409
    with final_core.connect() as db:
        db.execute("UPDATE candidate_findings SET status='archived' WHERE id=?",(c['id'],))
    assert client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'candidate_id':c['id']}).status_code==409


def test_concurrent_start_and_cancel(client,monkeypatch):
    run,c=candidate(client)
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    def start(_):
        return client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(start,[1,2]))
    assert first['id']==second['id'] and (first.get('reused') or second.get('reused'))
    assert client.post(f"/api/v1/guided-research/{first['id']}/cancel").json()['cancel_requested']
    guided.run_job(first['id'],guided.snapshot(run['id']))
    assert client.get(f"/api/v1/guided-research/{first['id']}").json()['status']=='cancelled'


def test_restart_recovers_without_auto_execution(client,monkeypatch):
    run,c=candidate(client)
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    job=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json()
    guided.init_guided_db()
    assert client.get(f"/api/v1/guided-research/{job['id']}").json()['status']=='interrupted'
    next_job=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json()
    assert next_job['id']!=job['id']
    guided.run_job(next_job['id'],guided.snapshot(run['id']))


def test_page_discovery_never_executes_or_leaks_query():
    parser=guided.PageLinks('https://example.test/page')
    parser.feed('<a href="/api?token=secret">x</a><script src="https://evil.test/x"></script><form action="/delete"></form><a href="javascript:alert(1)">x</a>')
    assert [x['url'] for x in parser.links]==['https://example.test/api','https://example.test/delete']
    assert all(x['executed'] is False for x in parser.links)


def test_missing_material_is_not_fabricated(client):
    run,c=candidate(client)
    with final_core.connect() as db:
        db.execute('UPDATE candidate_findings SET target=?,evidence_ids=? WHERE id=?',('https://example.test/missing','["nonexistent"]',c['id']))
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    item=job['result']['items'][0]
    assert item['sources']==[] and item['draft']['observed_behavior'] is None
    assert item['missing_evidence_ids']==['nonexistent']
    assert item['status']=='needs_material'


def test_information_observation_not_promoted(client):
    run,c=candidate(client,category='site_structure')
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    item=job['result']['items'][0]
    assert item['draft']['verification_method']=='observation_review'
    assert any(x['code']=='security_boundary' for x in item['gaps'])


def test_empty_run_is_not_a_safety_claim(client):
    run,c=candidate(client)
    with final_core.connect() as db:
        db.execute("UPDATE candidate_findings SET status='archived' WHERE id=?",(c['id'],))
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    assert job['status']=='completed' and job['result']['items']==[]
    assert job['result']['verification_executed'] is False


def test_failure_is_visible_and_retryable(client,monkeypatch):
    run,c=candidate(client)
    monkeypatch.setattr(guided,'material_for',lambda *args: (_ for _ in ()).throw(ValueError('secret-token-do-not-leak')))
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    assert job['status']=='failed'
    assert job['steps'][2]['status']=='failed'
    assert 'secret-token' not in str(job)


def test_unrelated_candidate_history_is_not_returned(client):
    run,c=candidate(client)
    other=client.post(f"/api/v1/runs/{run['id']}/candidates",json={'title':'Other candidate','category':'ssrf','target':'https://example.test/other','hypothesis':'Unrelated hypothesis','observation_ids':c['observation_ids']}).json()
    first=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'candidate_id':c['id']}).json())
    wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'candidate_id':other['id']}).json())
    assert client.get(f"/api/v1/runs/{run['id']}/guided-research?candidate_id={c['id']}").json()['id']==first['id']


def test_concurrency_limit_is_enforced(client,monkeypatch):
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    for _ in range(4):
        run,c=candidate(client)
        assert client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).status_code==202
    run,c=candidate(client)
    assert client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).status_code==409
    guided.init_guided_db()


def test_material_collection_never_calls_target_transport(client,monkeypatch):
    import traditional_runtime
    monkeypatch.setattr(traditional_runtime,'request_once',lambda *args: pytest.fail('unexpected target request'))
    run,c=candidate(client)
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    assert job['status']=='awaiting_input'


def test_worker_start_failure_does_not_leave_active_job(client,monkeypatch):
    run,c=candidate(client)
    monkeypatch.setattr(guided,'dispatch',lambda *args: (_ for _ in ()).throw(RuntimeError('thread unavailable')))
    response=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json()
    assert response['status']=='failed'
    assert response['error']=='worker_start_failed'


def test_automatic_flow_continues_verification_before_next_candidate_page():
    data={'auto_continue':True,'automation_round':4}
    result={'pending_verifications':2,'after_candidate_id':'candidate-100','next_cursor':'candidate-200'}
    body=guided.next_automation_input('guided-parent',data,result)
    assert body.auto_continue is True and body.automation_round==5
    assert body.continue_from=='guided-parent'
    assert body.after_candidate_id=='candidate-100'


def test_automatic_flow_moves_to_next_candidate_page_and_stops_at_bound():
    body=guided.next_automation_input('guided-parent',{'auto_continue':True,'automation_round':2},{'pending_verifications':0,'next_cursor':'candidate-200'})
    assert body.after_candidate_id=='candidate-200' and body.continue_from is None
    assert guided.next_automation_input('guided-parent',{'auto_continue':True,'automation_round':guided.MAX_AUTOMATION_ROUNDS},{'pending_verifications':1}) is None
