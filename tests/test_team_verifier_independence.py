import json
import pytest
import final_core
from tests.test_final import client
from tests.test_research_draft_review import saved_draft


def request_body(task):
    result=json.loads(task['result_json'])
    inputs=json.loads(task['context_capsule_json'])['research_input_hashes']
    return result['claim_ids'][0],{'campaign_id':task['campaign_id'],'evidence_ids':list(inputs),
        'replay_contract':{'type':'manual_evidence_review_v1'}}


@pytest.mark.parametrize('legacy',[False,True])
def test_team_producer_cannot_use_same_verifier_group(client,monkeypatch,legacy):
    task=saved_draft(client,monkeypatch)
    claim,body=request_body(task)
    if legacy:
        with final_core.connect() as db:
            attrs=json.loads(db.execute('SELECT attributes_json FROM research_nodes WHERE id=?',(claim,)).fetchone()[0])
            attrs.pop('producer_task_id',None);attrs.pop('producer_runner_ref',None)
            db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(json.dumps(attrs),claim))
    response=client.post('/api/v1/verification/claims/'+claim,json={**body,'verifier_group_id':task['group_id']})
    assert response.status_code==409
    assert 'producer task group' in response.json()['detail']
    accepted=client.post('/api/v1/verification/claims/'+claim,json=body)
    assert accepted.status_code==202,accepted.text
    with final_core.connect() as db:
        row=db.execute('SELECT producer_task_id,producer_runner_ref FROM verification_requests_v5').fetchone()
        assert row['producer_task_id']==task['id']
        assert row['producer_runner_ref']


@pytest.mark.parametrize('case',['omit_counter','edited_counter_refs','changed','missing','wrong_producer','wrong_runner'])
def test_team_verification_rejects_missing_counter_and_changed_inputs(client,monkeypatch,case):
    task=saved_draft(client,monkeypatch)
    claim,body=request_body(task)
    with final_core.connect() as db:
        if case in {'omit_counter','edited_counter_refs'}:
            counter=db.execute("SELECT id FROM research_nodes WHERE campaign_id=? AND node_type='counterevidence'",(task['campaign_id'],)).fetchone()[0]
            body['evidence_ids'].remove(counter)
            if case=='edited_counter_refs':
                attrs=json.loads(db.execute('SELECT attributes_json FROM research_nodes WHERE id=?',(claim,)).fetchone()[0]);attrs['counterevidence_ids']=[]
                db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(json.dumps(attrs),claim))
        elif case=='changed':db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(body['evidence_ids'][0],))
        elif case=='missing':db.execute('DELETE FROM research_nodes WHERE id=?',(body['evidence_ids'][0],))
        else:
            attrs=json.loads(db.execute('SELECT attributes_json FROM research_nodes WHERE id=?',(claim,)).fetchone()[0]);attrs['task_id' if case=='wrong_producer' else 'producer_runner_ref']='foreign-producer'
            db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(json.dumps(attrs),claim))
    response=client.post('/api/v1/verification/claims/'+claim,json=body)
    assert response.status_code in {404,409},response.text
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM verification_requests_v5').fetchone()[0]==0
