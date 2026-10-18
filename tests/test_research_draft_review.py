import json
import pytest
import final_core
import v5_research_worker as research
from tests.test_final import client
from tests.test_v5_research_worker import team, create, wait_group
from tests.test_v5_cloud_research import cloud_profile


def saved_draft(client, monkeypatch):
    campaign, body=team(client,count=1)
    body.update(runtime_profile_id=cloud_profile(client),allow_cloud_context=True,max_cost_micros=100000)
    with final_core.connect() as db:
        db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',(json.dumps({'sensitivity':'public'}),campaign['id']))
    def output(provider,task,nodes):
        return {'summary':'Review recorded observations only','hypotheses':[{
            'statement':'The recorded access requires an ownership review','scope':'Recorded evidence only',
            'evidence_ids':[x['id'] for x in nodes if x['node_type']=='evidence'],
            'counterevidence_ids':[x['id'] for x in nodes if x['node_type']=='counterevidence'],
            'limitations':['Not independently verified']}], 'open_questions':['Which access rule applies?']},40,30,5
    monkeypatch.setattr(research.w,'_local_model_output',output)
    group=create(client,body)
    assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code==202
    wait_group(group)
    with final_core.connect() as db:
        task=dict(db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone())
    assert task['status']=='succeeded'
    return task


def test_draft_review_preserves_readonly_material_lineage(client,monkeypatch):
    task=saved_draft(client,monkeypatch)
    path=f"/api/v1/workers/research/tasks/{task['id']}/draft"
    result=client.get(path)
    assert result.status_code==200,result.text
    value=result.json()
    assert value['inputs_current'] is True
    assert len(value['drafts'])==2 and len(value['inputs'])==2
    assert value['canonical_result_eligible'] is False and value['independently_verified'] is False
    assert {n['node_type'] for n in value['inputs']}=={'evidence','counterevidence'}
    with final_core.connect() as db:
        count=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
        db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(value['inputs'][0]['id'],))
    assert client.get(path).json()['inputs_current'] is False
    with final_core.connect() as db:
        db.execute('DELETE FROM research_nodes WHERE id=?',(value['inputs'][0]['id'],))
    changed=client.get(path).json()
    assert any(x['material_status']=='missing' for x in changed['inputs'])
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==count
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0


@pytest.mark.parametrize('change',['foreign_source','duplicate','oversized','wrong_run'])
def test_draft_review_rejects_corrupt_or_cross_context_references(client,monkeypatch,change):
    task=saved_draft(client,monkeypatch)
    result=json.loads(task['result_json']);claim=result['claim_ids'][0]
    with final_core.connect() as db:
        if change=='foreign_source':db.execute("UPDATE research_nodes SET source_ref='foreign-task:claim:0' WHERE id=?",(claim,))
        elif change=='wrong_run':db.execute("UPDATE research_nodes SET run_id=NULL WHERE id=?",(claim,))
        else:
            result['claim_ids']=[claim]*(2 if change=='duplicate' else 6)
            db.execute('UPDATE agent_tasks SET result_json=? WHERE id=?',(json.dumps(result),task['id']))
    assert client.get(f"/api/v1/workers/research/tasks/{task['id']}/draft").status_code==409
