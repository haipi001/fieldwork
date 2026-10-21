import copy
import pytest
import final_core as core
from tests.test_final import client
from tests.test_v5_http_receipts import controlled_server
from tests.test_reviewed_replay_batch import setup as batch_setup,wait


def test_explicit_selection_beyond_first_page_executes_only_selected_and_binds_receipt(client,monkeypatch,controlled_server):
    run,calls=batch_setup(client,monkeypatch,controlled_server,count=102)
    with core.connect() as db:
        ids=[row[0] for row in db.execute('SELECT id FROM candidate_findings WHERE run_id=? ORDER BY id',(run,))]
    wanted=ids[-2:];base=f'/api/v1/runs/{run}/candidate-workflow-batch'
    query=[('candidate_ids',x) for x in wanted]
    triage=client.get(f'/api/v1/runs/{run}/candidate-workflow',params=query)
    assert triage.status_code==200,triage.text
    assert {x['candidate_id'] for x in triage.json()['items']}==set(wanted)
    before=len(calls)
    plan=client.get(base+'/execution-plan',params=query);assert plan.status_code==200,plan.text
    value=plan.json();assert value['requested_candidate_ids']==wanted and value['selected_candidate_ids']==wanted
    assert len(calls)==before and value['next_cursor'] is None
    result=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':value['source_fingerprint'],'candidate_ids':wanted})
    assert result.status_code==202,result.text
    job=wait(client,result.json())
    assert len(calls)==before+20
    assert {x['candidate_id'] for x in job['result']['items']}==set(wanted)
    assert job['result']['requested_candidate_ids']==wanted
    assert all(x['auto_verification']['independent_verification'].get('receipt_id') for x in job['result']['items'])
    receipt_id=job['result']['items'][0]['auto_verification']['independent_verification']['receipt_id']
    receipt_url='/api/v1/verification/receipts/'+receipt_id
    assert client.get(receipt_url).json()['integrity']['current_inputs_match']
    for tamper in ([],[ids[0]],None):
        changed=copy.deepcopy(job['result'])
        if tamper is None:changed.pop('requested_candidate_ids')
        else:changed['requested_candidate_ids']=tamper
        with core.connect() as db:db.execute('UPDATE guided_research_jobs SET result=? WHERE id=?',(core.dump(changed),job['id']))
        assert client.get(receipt_url).json()['integrity']['current_inputs_match'] is False
    with core.connect() as db:db.execute('UPDATE guided_research_jobs SET result=? WHERE id=?',(core.dump(job['result']),job['id']))
    assert client.get(receipt_url).json()['integrity']['current_inputs_match']
    assert client.get(base+'/execution-plan',params=query).json()['can_execute'] is False
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==1


@pytest.mark.parametrize('change',['omitted','changed','foreign','duplicate','over_limit','cursor','archived'])
def test_selection_changes_never_fall_back_to_other_candidates(client,monkeypatch,controlled_server,change):
    run,calls=batch_setup(client,monkeypatch,controlled_server,count=4)
    with core.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM candidate_findings WHERE run_id=? ORDER BY id',(run,))]
    base=f'/api/v1/runs/{run}/candidate-workflow-batch';wanted=ids[-1:]
    plan=client.get(base+'/execution-plan',params=[('candidate_ids',x) for x in wanted]).json()
    body={'authorized':True,'source_fingerprint':plan['source_fingerprint'],'candidate_ids':wanted}
    if change=='omitted':body.pop('candidate_ids')
    elif change=='changed':body['candidate_ids']=ids[:1]
    elif change=='foreign':body['candidate_ids']=['foreign']
    elif change=='duplicate':body['candidate_ids']=wanted*2
    elif change=='over_limit':body['candidate_ids']=ids
    elif change=='cursor':body['after_candidate_id']=ids[0]
    else:
        with core.connect() as db:db.execute("UPDATE candidate_findings SET status='archived' WHERE id=?",(wanted[0],))
    before=len(calls);response=client.post(base+'/execute',json=body)
    assert response.status_code in (409,422),response.text
    assert len(calls)==before
    with core.connect() as db:assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==0


@pytest.mark.parametrize('reviewed',[True,False,None])
def test_identity_login_never_resumes_a_reviewed_selection_as_whole_run(client,monkeypatch,controlled_server,reviewed):
    import guided_research as guided
    run,calls=batch_setup(client,monkeypatch,controlled_server,count=4)
    with core.connect() as db:
        identity=db.execute('SELECT i.id FROM identities i JOIN analysis_runs r ON r.engagement_id=i.engagement_id WHERE r.id=? LIMIT 1',(run,)).fetchone()[0]
        result={'execute_ready':True,'reviewed_execution':reviewed,'requested_candidate_ids':['wanted'],
            'items':[{'candidate_id':'wanted','gaps':[{'code':'identities'}]}]}
        now=core.utcnow()
        db.execute('INSERT INTO guided_research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('reviewed-awaiting',run,None,'reviewed-fixture','awaiting_input','Needs login','[]',core.dump(result),0,None,now,now))
    monkeypatch.setattr(guided,'start_job',lambda *_args,**_kwargs:pytest.fail('Reviewed jobs must receive fresh explicit approval'))
    before=len(calls)
    assert guided.resume_after_identity(identity)==[]
    assert len(calls)==before
