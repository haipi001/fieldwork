import json
import pytest
import final_core as core
import v5_research_worker as research
from tests.test_final import client
from tests.test_campaign_materials import fixture_materials,create as create_campaign
from tests.test_v5_team_plan import _body
from tests.test_v5_research_worker import create,wait_group
from tests.test_v5_cloud_research import cloud_profile


def draft(client,monkeypatch,extra_target=False):
    project,old,run,path,aid,oid,url=fixture_materials(client)
    with core.connect() as db:
        db.execute("UPDATE observations SET subject='https://team.example.test/object',observation_type='authorization' WHERE id=?",(oid,))
    if extra_target:
        with core.connect() as db:
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(core.uid('obs'),run,project['id'],'traditional','authorization','https://team.example.test/other','Other object response',.5,'fixture',aid,core.utcnow()))
    preview=client.get(url).json();campaign=create_campaign(client,project,run,preview).json()
    with core.connect() as db:
        for row in db.execute('SELECT id,attributes_json FROM research_nodes WHERE campaign_id=?',(campaign['id'],)).fetchall():
            attrs=core.load(row['attributes_json']);attrs['sensitivity']='public'
            db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(core.dump(attrs),row['id']))
        counter=db.execute("SELECT source_ref FROM research_nodes WHERE campaign_id=? AND node_type='counterevidence'",(campaign['id'],)).fetchone()[0]
    body=_body(campaign,run,1);body.update(runtime_profile_id=cloud_profile(client),allow_cloud_context=True,max_cost_micros=100000)
    def output(provider,task,nodes):
        return {'summary':'Recorded ownership needs review','hypotheses':[{
            'statement':'The recorded object may require a scoped ownership check','scope':'Selected recorded object only',
            'evidence_ids':[n['id'] for n in nodes if n['node_type']=='observation'],
            'counterevidence_ids':[n['id'] for n in nodes if n['node_type']=='counterevidence'],
            'limitations':['This is not independently verified']}],'open_questions':[]},40,30,5
    monkeypatch.setattr(research.w,'_local_model_output',output)
    group=create(client,body)
    assert client.post(f'/api/v1/workers/research/groups/{group}/start').status_code==202
    wait_group(group)
    with core.connect() as db:
        task=dict(db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone())
    assert task['status']=='succeeded'
    claim=core.load(task['result_json'])['claim_ids'][0]
    return f"/api/v1/workers/research/tasks/{task['id']}/drafts/{claim}",path,oid,counter,claim


def test_reviewed_draft_creates_idempotent_unverified_candidate_with_original_counterevidence(client,monkeypatch):
    base,path,oid,counter,claim=draft(client,monkeypatch)
    result=client.get(base+'/candidate-plan');assert result.status_code==200,result.text
    plan=result.json();assert plan['can_create'] and plan['proposal']['category']=='authorization'
    assert plan['counterevidence_ids']==[counter] and plan['executes_tools'] is False
    assert str(path) not in result.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        calls=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
    payload={'authorized':True,'target':plan['proposal']['target'],'preview_hash':plan['preview_hash']}
    created=client.post(base+'/candidate',json=payload);assert created.status_code==201,created.text
    value=created.json();assert value['created'] and counter in value['evidence_ids']
    assert client.post(base+'/candidate',json=payload).json()['created'] is False
    with core.connect() as db:
        candidate=db.execute('SELECT * FROM candidate_findings').fetchone()
        assert candidate['status']=='candidate' and candidate['category']=='authorization'
        evidence=[dict(row) for row in db.execute('SELECT * FROM evidence_v2 WHERE id IN (SELECT value FROM json_each(?))',(candidate['evidence_ids'],))]
        assert len(evidence)==2 and {r['polarity'] for r in evidence}=={'supporting','counterevidence'}
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==calls
        assert db.execute("SELECT count(*) FROM run_events_v2 WHERE kind='research.candidate_reviewed'").fetchone()[0]==1


@pytest.mark.parametrize('change',['authorization','file','scope','counter','target','failed_task','campaign_archived'])
def test_changed_or_unapproved_draft_never_creates_candidate(client,monkeypatch,change):
    base,path,oid,counter,claim=draft(client,monkeypatch)
    plan=client.get(base+'/candidate-plan').json()
    payload={'authorized':True,'target':plan['proposal']['target'],'preview_hash':plan['preview_hash']}
    if change=='authorization':payload['authorized']=False
    elif change=='file':path.write_bytes(b'changed')
    elif change=='target':payload['target']='https://outside.test/object'
    else:
        with core.connect() as db:
            if change=='campaign_archived':db.execute("UPDATE research_campaigns SET status='archived'")
            elif change=='failed_task':db.execute("UPDATE agent_tasks SET status='paused'")
            elif change=='scope':db.execute("UPDATE analysis_runs SET scope_snapshot_id='stale'")
            else:
                attrs=core.load(db.execute('SELECT attributes_json FROM research_nodes WHERE id=?',(claim,)).fetchone()[0]);attrs['counterevidence_ids']=[]
                db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(core.dump(attrs),claim))
    response=client.post(base+'/candidate',json=payload)
    assert response.status_code==(422 if change=='authorization' else 409),response.text
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0


def test_material_change_during_candidate_creation_rolls_back(client,monkeypatch):
    import research_candidate_handoff as handoff
    base,path,oid,counter,claim=draft(client,monkeypatch);plan=client.get(base+'/candidate-plan').json()
    original=handoff.plan;calls=[]
    def changed(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:path.write_bytes(b'changed after insert')
        return original(*args,**kwargs)
    monkeypatch.setattr(handoff,'plan',changed)
    result=client.post(base+'/candidate',json={'authorized':True,'target':plan['proposal']['target'],'preview_hash':plan['preview_hash']})
    assert result.status_code==409,result.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM evidence_v2').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM run_events_v2 WHERE kind='research.candidate_reviewed'").fetchone()[0]==0


def test_multiple_registered_targets_require_explicit_selection(client,monkeypatch):
    base,path,oid,counter,claim=draft(client,monkeypatch,extra_target=True)
    first=client.get(base+'/candidate-plan').json()
    assert len(first['targets'])==2 and first['proposal']['target'] is None and first['can_create'] is False
    chosen=first['targets'][1]
    second=client.get(base+'/candidate-plan',params={'target':chosen}).json()
    assert second['can_create'] and second['preview_hash']!=first['preview_hash']
    result=client.post(base+'/candidate',json={'authorized':True,'target':chosen,'preview_hash':second['preview_hash']})
    assert result.status_code==201,result.text
    with core.connect() as db:
        assert db.execute('SELECT target FROM candidate_findings').fetchone()[0]==chosen
