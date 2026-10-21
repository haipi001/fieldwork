import json
import copy
import threading
import pytest
import final_core as core
from tests.test_final import client

from tests.test_v5_http_receipts import controlled_server
from tests.test_v5_http_workflow import prepared_live_candidate


def wait(client,job):
    worker=next((t for t in threading.enumerate() if t.name==job['id']),None)
    if worker:
        worker.join(timeout=30)
        if worker.is_alive():
            client.post(f"/api/v1/guided-research/{job['id']}/cancel")
            worker.join(timeout=20)
        assert not worker.is_alive(),'Owned batch worker still running; no duplicate started'
    result=client.get(f"/api/v1/guided-research/{job['id']}").json()
    assert result['status'] not in ('queued','running'),result['status']
    return result


def setup(client,monkeypatch,controlled_server,count=1,access='owner_only'):
    run,candidate,project,calls=prepared_live_candidate(client,monkeypatch,controlled_server[0],access=access)
    with core.connect() as db:
        source=dict(db.execute('SELECT * FROM candidate_findings WHERE id=?',(candidate['id'],)).fetchone())
        for i in range(count-1):
            row=source|{'id':f'batch-candidate-{i}','title':f'Additional boundary review {i}'}
            db.execute('INSERT INTO candidate_findings ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        db.execute('UPDATE run_budgets_v2 SET request_limit=45 WHERE run_id=?',(run,))
    return run,calls


def test_reviewed_batch_is_readonly_bounded_and_retains_unselected(client,monkeypatch,controlled_server):
    run,calls=setup(client,monkeypatch,controlled_server,4)
    base=f'/api/v1/runs/{run}/candidate-workflow-batch'
    before=len(calls)
    plan=client.get(base+'/execution-plan').json()
    assert len(calls)==before==3
    assert len(plan['items'])==4 and len(plan['selected_candidate_ids'])==3
    assert all(item['scope_snapshot_id'] and item['business_rule']['access']=='owner_only' for item in plan['items'])
    assert all(len(item['requests'])==5 and all('headers' not in request for request in item['requests']) for item in plan['items'])
    assert plan['request_limit']==30 and plan['continues_without_review'] is False
    with core.connect() as db:assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==0
    response=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':plan['source_fingerprint']})
    assert response.status_code==202,response.text
    job=wait(client,response.json())
    assert job['result']['reviewed_execution'] is True and job['result']['auto_continue'] is False
    assert len(calls)==33
    assert set(job['result']['reviewed_candidate_ids'])==set(plan['selected_candidate_ids'])
    completed=[item for item in job['result']['items'] if item.get('auto_verification')]
    assert len(completed)==3
    assert all(item['auto_verification']['independent_verification'].get('receipt_id') for item in completed),job
    deferred=[item for item in job['result']['items'] if item['candidate_id'] not in plan['selected_candidate_ids']]
    assert len(deferred)==1 and any(gap['code']=='batch_not_selected' for gap in deferred[0]['gaps'])
    next_plan=client.get(base+'/execution-plan').json()
    deferred_ids={x['candidate_id'] for x in job['result']['items'] if x['candidate_id'] not in plan['selected_candidate_ids']}
    assert set(next_plan['selected_candidate_ids'])==deferred_ids,next_plan
    assert sum('current_receipt' in x['blockers'] for x in next_plan['items'])==3
    second=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':next_plan['source_fingerprint']})
    assert second.status_code==202,second.text
    final=wait(client,second.json())
    assert len(calls)==43
    assert final['result']['reviewed_candidate_ids']==list(deferred_ids)
    assert sum(bool(x.get('auto_verification')) for x in final['result']['items'])==1
    assert client.get(base+'/execution-plan').json()['can_execute'] is False
    receipt_id=completed[0]['auto_verification']['independent_verification']['receipt_id']
    receipt_path='/api/v1/verification/receipts/'+receipt_id
    assert client.get(receipt_path).json()['integrity']['current_inputs_match'] is True
    for change in ('request_total','selection','artifact'):
        altered=copy.deepcopy(job['result'])
        if change=='request_total':altered['requests_sent']-=1
        elif change=='selection':altered['reviewed_candidate_ids']=[]
        else:
            item=next(x for x in altered['items'] if x['candidate_id']==completed[0]['candidate_id'])
            item['replay_checkpoint']['final_artifact_id']='foreign-artifact'
        with core.connect() as db:db.execute('UPDATE guided_research_jobs SET result=? WHERE id=?',(core.dump(altered),job['id']))
        assert client.get(receipt_path).json()['integrity']['current_inputs_match'] is False
    with core.connect() as db:db.execute('UPDATE guided_research_jobs SET result=? WHERE id=?',(core.dump(job['result']),job['id']))
    assert client.get(receipt_path).json()['integrity']['current_inputs_match'] is True
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==2


@pytest.mark.parametrize('change',['authorization','fingerprint','evidence','budget','receipt'])
def test_reviewed_batch_rejects_stale_or_unapproved_selection(client,monkeypatch,controlled_server,change):
    run,calls=setup(client,monkeypatch,controlled_server)
    base=f'/api/v1/runs/{run}/candidate-workflow-batch'
    plan=client.get(base+'/execution-plan').json();payload={'authorized':True,'source_fingerprint':plan['source_fingerprint']}
    if change=='authorization':payload['authorized']=False
    elif change=='fingerprint':payload['source_fingerprint']='0'*64
    elif change=='receipt':
        import guided_research
        monkeypatch.setattr(guided_research,'_current_batch_receipt',lambda *_:'new-current-receipt')
    else:
        with core.connect() as db:
            if change=='evidence':db.execute("UPDATE observations SET summary='Changed after batch review' WHERE run_id=?",(run,))
            else:db.execute('UPDATE run_budgets_v2 SET request_limit=3 WHERE run_id=?',(run,))
    result=client.post(base+'/execute',json=payload)
    assert result.status_code==(422 if change=='authorization' else 409),result.text
    assert len(calls)==3
    with core.connect() as db:assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==0


def test_reviewed_batch_excludes_legitimate_sharing(client,monkeypatch,controlled_server):
    run,calls=setup(client,monkeypatch,controlled_server,access='allowlist')
    base=f'/api/v1/runs/{run}/candidate-workflow-batch'
    plan=client.get(base+'/execution-plan').json()
    assert plan['can_execute'] is False and not plan['selected_candidate_ids']
    assert 'business_access_permitted' in plan['items'][0]['blockers']
    result=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':plan['source_fingerprint']})
    assert result.status_code==409 and len(calls)==3


def test_reviewed_batch_lists_duplicates_without_dispatching_twice(client,monkeypatch,controlled_server):
    run,calls=setup(client,monkeypatch,controlled_server,2)
    with core.connect() as db:db.execute("UPDATE candidate_findings SET title='Cross identity read fixture' WHERE run_id=?",(run,))
    plan=client.get(f'/api/v1/runs/{run}/candidate-workflow-batch/execution-plan').json()
    assert len(plan['items'])==2 and len(plan['selected_candidate_ids'])==1
    assert sum('duplicate' in item['blockers'] for item in plan['items'])==1
    assert len(calls)==3


def test_reviewed_batch_pages_beyond_one_hundred_and_binds_cursor(client,monkeypatch,controlled_server):
    run,calls=setup(client,monkeypatch,controlled_server,101)
    base=f'/api/v1/runs/{run}/candidate-workflow-batch'
    first=client.get(base+'/execution-plan').json()
    assert len(first['items'])==100 and first['remaining_candidates']==1 and first['next_cursor']
    cursor=first['next_cursor']
    response=client.get(base+'/execution-plan',params={'after_candidate_id':cursor})
    assert response.status_code==200,response.text
    second=response.json()
    assert len(second['items'])==1 and not second['next_cursor'] and second['remaining_candidates']==0
    assert not {x['candidate_id'] for x in first['items']} & {x['candidate_id'] for x in second['items']}
    assert second['source_fingerprint']!=first['source_fingerprint'] and len(calls)==3
    assert client.get(base+'/execution-plan',params={'after_candidate_id':'foreign-candidate'}).status_code==409
    wrong=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':second['source_fingerprint']})
    assert wrong.status_code==409 and len(calls)==3
    with core.connect() as db:assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0]==0
    sent=client.post(base+'/execute',json={'authorized':True,'source_fingerprint':second['source_fingerprint'],'after_candidate_id':cursor})
    assert sent.status_code==202,sent.text
    job=wait(client,sent.json())
    assert job['result']['after_candidate_id']==cursor
    assert len(job['result']['items'])==1 and len(calls)==13
    assert job['result']['items'][0]['auto_verification']['independent_verification'].get('receipt_id'),job['phase']
