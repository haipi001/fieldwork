import json
import pytest
import final_core as core
from tests.test_final import client
from tests.test_research_candidate_handoff import draft


def fixture(client,monkeypatch,count=2):
    base,path,oid,counter,claim=draft(client,monkeypatch)
    task_id=base.split('/tasks/')[1].split('/')[0]
    with core.connect() as db:
        task=dict(db.execute('SELECT * FROM agent_tasks WHERE id=?',(task_id,)).fetchone())
        original=dict(db.execute('SELECT * FROM research_nodes WHERE id=?',(claim,)).fetchone())
        result=core.load(task['result_json'])
        for i in range(1,count):
            row=original|{'id':core.uid('claim'),'source_ref':f'{task_id}:claim:{i}','title':f'Independent draft {i}','body':f'Another supported research hypothesis {i}'}
            db.execute('INSERT INTO research_nodes('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
            result['claim_ids'].append(row['id'])
        db.execute('UPDATE agent_tasks SET result_json=? WHERE id=?',(core.dump(result),task_id))
    return '/api/v1/workers/research/groups/'+task['group_id'],[{'task_id':task_id,'claim_id':c} for c in result['claim_ids']],path,counter


def test_atomic_batch_preserves_counterevidence_is_idempotent_and_does_not_execute(client,monkeypatch):
    base,items,path,counter=fixture(client,monkeypatch)
    options=client.get(base+'/candidate-handoff-options');assert options.status_code==200,options.text
    assert len(options.json()['items'])==2 and options.json()['source_verification']=='not_checked_until_plan'
    with core.connect() as db:calls=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
    preview=client.post(base+'/candidate-handoff-plan',json={'items':items});assert preview.status_code==200,preview.text
    plan=preview.json();assert plan['can_create'] and plan['atomic'] and not plan['executes_tools']
    payload={'items':items,'preview_hash':plan['preview_hash'],'authorized':True}
    response=client.post(base+'/candidate-handoff',json=payload);assert response.status_code==201,response.text
    assert all(row['created'] and counter in row['evidence_ids'] for row in response.json()['items'])
    retry=client.post(base+'/candidate-handoff',json=payload);assert retry.status_code==201,retry.text
    assert all(not row['created'] for row in retry.json()['items'])
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==calls
        for table in ('verification_receipts_v5','canonical_findings','guided_research_jobs'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


@pytest.mark.parametrize('change',['file','group','task','claim','target','selection','unauthorized','string_authorized'])
def test_changed_batch_or_unapproved_submission_saves_nothing(client,monkeypatch,change):
    base,items,path,counter=fixture(client,monkeypatch)
    plan=client.post(base+'/candidate-handoff-plan',json={'items':items}).json()
    body={'items':items,'preview_hash':plan['preview_hash'],'authorized':True}
    if change=='file':path.write_bytes(b'changed')
    elif change=='group':
        with core.connect() as db:db.execute("UPDATE research_groups SET status='paused'")
    elif change=='task':
        with core.connect() as db:db.execute("UPDATE agent_tasks SET status='paused'")
    elif change=='claim':
        with core.connect() as db:db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(items[1]['claim_id'],))
    elif change=='target':items[1]['target']='https://outside.test/object'
    elif change=='selection':body['items']=items[:1]
    elif change=='unauthorized':body['authorized']=False
    else:body['authorized']='true'
    response=client.post(base+'/candidate-handoff',json=body)
    assert response.status_code==(422 if change in {'unauthorized','string_authorized'} else 409),response.text
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0


def test_failure_after_first_insert_rolls_back_entire_batch(client,monkeypatch):
    import research_candidate_handoff as single
    base,items,path,counter=fixture(client,monkeypatch)
    plan=client.post(base+'/candidate-handoff-plan',json={'items':items}).json()
    real=single.create_in_transaction;count=0
    def changed_after_first(*args,**kwargs):
        nonlocal count
        value=real(*args,**kwargs);count+=1
        if count==1:path.write_bytes(b'changed during batch')
        return value
    monkeypatch.setattr(single,'create_in_transaction',changed_after_first)
    result=client.post(base+'/candidate-handoff',json={'items':items,'preview_hash':plan['preview_hash'],'authorized':True})
    assert result.status_code==409,result.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM run_events_v2 WHERE kind='research.candidate_reviewed'").fetchone()[0]==0


def test_duplicate_over_limit_foreign_task_and_empty_selection_rejected(client,monkeypatch):
    base,items,path,counter=fixture(client,monkeypatch)
    for selected in ([],items*2,[items[0],items[0]]):
        assert client.post(base+'/candidate-handoff-plan',json={'items':selected}).status_code==422
    assert client.post(base+'/candidate-handoff-plan',json={'items':[items[0]|{'task_id':'foreign'}]}).status_code==409
    assert client.get(base+'/candidate-handoff-options?offset=-1').status_code==422


def test_pagination_and_cross_task_selection_stay_bound_to_same_group_and_run(client,monkeypatch):
    base,items,path,counter=fixture(client,monkeypatch,count=1)
    with core.connect() as db:
        task=dict(db.execute('SELECT * FROM agent_tasks WHERE id=?',(items[0]['task_id'],)).fetchone())
        original=dict(db.execute('SELECT * FROM research_nodes WHERE id=?',(items[0]['claim_id'],)).fetchone())
        for i in range(6):
            new_id='task-batch-paging-'+str(i);claim_id=core.uid('claim')
            result=core.load(task['result_json']);result['claim_ids']=[claim_id]
            clone=task|{'id':new_id,'idempotency_key':'paging-'+str(i),'result_json':core.dump(result)}
            db.execute('INSERT INTO agent_tasks('+','.join(clone)+') VALUES('+','.join('?' for _ in clone)+')',tuple(clone.values()))
            attrs=core.load(original['attributes_json']);attrs.update(task_id=new_id,producer_task_id=new_id)
            row=original|{'id':claim_id,'source_ref':new_id+':claim:0','attributes_json':core.dump(attrs)}
            db.execute('INSERT INTO research_nodes('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
    first=client.get(base+'/candidate-handoff-options').json()
    second=client.get(base+'/candidate-handoff-options?offset='+str(first['next_offset'])).json()
    assert first['next_offset']==5 and second['next_offset'] is None
    assert len(first['items'])==5 and len(second['items'])==2
    selected=[{k:row[k] for k in ('task_id','claim_id')} for row in (first['items'][0],second['items'][-1])]
    assert selected[0]['task_id']!=selected[1]['task_id']
    preview=client.post(base+'/candidate-handoff-plan',json={'items':selected});assert preview.status_code==200,preview.text
    result=client.post(base+'/candidate-handoff',json={'items':selected,'preview_hash':preview.json()['preview_hash'],'authorized':True})
    assert result.status_code==201,result.text
    assert len(result.json()['items'])==2


@pytest.mark.parametrize('change',['synthetic','web3','archived','cancelled'])
def test_batch_never_accepts_wrong_domain_demo_or_inactive_context(client,monkeypatch,change):
    base,items,path,counter=fixture(client,monkeypatch)
    with core.connect() as db:
        if change=='synthetic':db.execute('UPDATE analysis_runs SET synthetic=1')
        elif change=='web3':db.execute("UPDATE analysis_runs SET mode='web3'")
        elif change=='archived':db.execute("UPDATE research_campaigns SET status='archived'")
        else:db.execute("UPDATE research_groups SET status='cancelled'")
    assert client.get(base+'/candidate-handoff-options').status_code==409
    assert client.post(base+'/candidate-handoff-plan',json={'items':items}).status_code==409
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0


def test_prepare_selects_at_most_three_and_requires_existing_explicit_save(client,monkeypatch):
    base,items,path,counter=fixture(client,monkeypatch,count=5)
    with core.connect() as db:
        before={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('candidate_findings','runtime_calls','verification_receipts_v5','canonical_findings')}
    prepared=client.get(base+'/candidate-handoff-prepare');assert prepared.status_code==200,prepared.text
    value=prepared.json();assert len(value['selection'])==3 and value['not_examined']==2
    assert value['selection_order']=='saved_task_id_then_claim_order'
    assert value['plan']['can_create'] and not value['saves_candidates'] and not value['executes_tools']
    with core.connect() as db:
        assert before=={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
    payload={'items':value['selection'],'preview_hash':value['plan']['preview_hash'],'authorized':False}
    assert client.post(base+'/candidate-handoff',json=payload).status_code==422
    payload['authorized']=True
    saved=client.post(base+'/candidate-handoff',json=payload);assert saved.status_code==201,saved.text
    again=client.get(base+'/candidate-handoff-prepare').json()
    assert len(again['selection'])==2 and len(again['skipped'])==3
    assert all(row['reason']=='candidate_already_saved' for row in again['skipped'])
    path.write_bytes(b'changed after automatic preparation')
    assert client.post(base+'/candidate-handoff',json={'items':again['selection'],'preview_hash':again['plan']['preview_hash'],'authorized':True}).status_code==409
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==3


def test_prepare_reports_invalid_sources_and_keeps_manual_options(client,monkeypatch):
    base,items,path,counter=fixture(client,monkeypatch)
    path.write_bytes(b'changed source')
    response=client.get(base+'/candidate-handoff-prepare');assert response.status_code==200,response.text
    value=response.json();assert value['selection']==[] and value['plan'] is None
    assert len(value['skipped'])==2 and all(row['reason']=='sources_or_authority_require_review' for row in value['skipped'])
    assert len(client.get(base+'/candidate-handoff-options').json()['items'])==2
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0


def test_prepare_does_not_guess_ambiguous_target(client,monkeypatch):
    base,path,oid,counter,claim=draft(client,monkeypatch,extra_target=True)
    task_id=base.split('/tasks/')[1].split('/')[0]
    with core.connect() as db:group=db.execute('SELECT group_id FROM agent_tasks WHERE id=?',(task_id,)).fetchone()[0]
    response=client.get('/api/v1/workers/research/groups/'+group+'/candidate-handoff-prepare')
    assert response.status_code==200,response.text
    assert response.json()['selection']==[]
    assert response.json()['skipped'][0]['reason']=='target_requires_manual_selection'
