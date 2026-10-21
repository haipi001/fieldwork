import pytest
import final_core as core
import research_resume as resume
from tests.test_final import client
from tests.test_research_candidate_handoff import draft
from tests.test_v5_team_plan import _body
from tests.test_v5_research_worker import create,wait_group


def pending(client,monkeypatch,extra_target=False,count=1,information_only=False):
    base,path,oid,counter,claim=draft(client,monkeypatch,extra_target=extra_target)
    task_id=base.split('/tasks/')[1].split('/')[0]
    with core.connect() as db:
        old=db.execute('SELECT * FROM agent_tasks WHERE id=?',(task_id,)).fetchone()
        group=db.execute('SELECT * FROM research_groups WHERE id=?',(old['group_id'],)).fetchone()
        campaign=dict(db.execute('SELECT * FROM research_campaigns WHERE id=?',(old['campaign_id'],)).fetchone())
        if information_only:
            db.execute("UPDATE observations SET observation_type='surface.api_description' WHERE id=?",(oid,))
            for node in db.execute("SELECT id,attributes_json FROM research_nodes WHERE campaign_id=? AND source_type='observation' AND source_ref=?",(campaign['id'],oid)).fetchall():
                attrs=core.load(node['attributes_json']);attrs['observation_type']='surface.api_description'
                db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(core.dump(attrs),node['id']))
        body=_body(campaign,old['run_id'],count)
        body.update(runtime_profile_id=group['runtime_profile_id'],allow_cloud_context=True,max_cost_micros=100000,max_concurrency=1)
    body['objective']='New separately approved automatic candidate group'
    plan=client.post('/api/v1/orchestration/groups/team/preview',json=body);assert plan.status_code==200,plan.text
    saved=client.post('/api/v1/orchestration/groups/team',json=body|{'preview_hash':plan.json()['preview_hash'],'idempotency_key':'auto-candidate-new-group'});assert saved.status_code==201,saved.text
    group=saved.json()['group']['id']
    return group,'/api/v1/workers/research/groups/'+group,path


def approve(client,base,limit=1):
    response=client.get(base+'/resume-plan',params={'candidate_limit':limit});assert response.status_code==200,response.text
    response=client.post(base+'/resume-policy',json={'confirmed':True,'preview_hash':response.json()['preview_hash'],'candidate_limit':limit,'candidate_creation_confirmed':True})
    assert response.status_code==200,response.text
    return response.json()


def test_separate_consent_hash_binding_and_no_default_expansion(client,monkeypatch):
    group,base,path=pending(client,monkeypatch)
    plan=client.get(base+'/resume-plan',params={'candidate_limit':1});assert plan.status_code==200,plan.text
    body={'confirmed':True,'preview_hash':plan.json()['preview_hash'],'candidate_limit':1}
    assert client.post(base+'/resume-policy',json=body).status_code==422
    body.update(candidate_creation_confirmed=True,candidate_limit=2)
    assert client.post(base+'/resume-policy',json=body).status_code==409
    for invalid in (-1,4,'1',True):
        assert client.post(base+'/resume-policy',json=body|{'candidate_limit':invalid}).status_code==422
    approve(client,base,0)
    resume.tick();wait_group(group);resume.tick()
    assert client.get(base+'/resume-policy').json()['candidate_limit']==0
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0


def test_approved_tasks_create_capped_unverified_candidate_once(client,monkeypatch):
    group,base,path=pending(client,monkeypatch,count=2);approve(client,base,1)
    resume.tick();wait_group(group)
    with core.connect() as db:calls=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
    assert resume.tick()==[{'group_id':group,'state':'completed'}]
    status=client.get(base+'/resume-policy').json();saved=status['candidate_handoff']
    assert saved['state']=='limit_reached' and len(saved['candidate_ids'])==1
    assert resume.tick()==[]
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==1
        assert db.execute('SELECT status FROM candidate_findings').fetchone()[0]=='candidate'
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==calls
        assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0


@pytest.mark.parametrize('change',['file','scope','disabled','task'])
def test_changed_or_disabled_policy_never_saves_candidates(client,monkeypatch,change):
    group,base,path=pending(client,monkeypatch);policy=approve(client,base)
    resume.tick();wait_group(group)
    if change=='file':path.write_bytes(b'changed source after model')
    elif change=='disabled':client.post(base+'/resume-policy/disable',json={'revision':policy['revision']})
    else:
        with core.connect() as db:
            if change=='scope':db.execute("UPDATE analysis_runs SET scope_snapshot_id='changed'")
            else:db.execute("UPDATE agent_tasks SET objective='changed' WHERE group_id=?",(group,))
    resume.tick()
    with core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
    assert client.get(base+'/resume-policy').json()['state']==('disabled' if change=='disabled' else 'blocked')


def test_ambiguous_targets_are_retained_for_manual_review(client,monkeypatch):
    group,base,path=pending(client,monkeypatch,extra_target=True);approve(client,base)
    resume.tick();wait_group(group);resume.tick()
    value=client.get(base+'/resume-policy').json()['candidate_handoff']
    assert value['state']=='needs_review' and value['review_required']==1 and value['candidate_ids']==[]


def test_failure_during_handoff_rolls_back_candidates_and_cursor(client,monkeypatch):
    import research_candidate_handoff as handoff
    group,base,path=pending(client,monkeypatch,count=2);approve(client,base,2)
    resume.tick();wait_group(group)
    original=handoff.create_in_transaction
    def change(*args,**kwargs):
        result=original(*args,**kwargs);path.write_bytes(b'changed during candidate save');return result
    monkeypatch.setattr(handoff,'create_in_transaction',change)
    resume.tick()
    assert client.get(base+'/resume-policy').json()['state']=='blocked'
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM run_events_v2 WHERE kind='research.candidate_reviewed'").fetchone()[0]==0
        assert resume.read(db,group).get('candidate_handoff') is None


def test_candidate_cursor_is_persisted_and_bounded_to_five_tasks_per_pass(client,monkeypatch):
    import v5_research_worker as worker
    group,base,path=pending(client,monkeypatch,count=6)
    monkeypatch.setattr(worker.w,'_local_model_output',lambda *_:({'summary':'No hypothesis in this fixture','hypotheses':[],'open_questions':[]},5,5,1))
    approve(client,base,3);resume.tick();wait_group(group)
    with core.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT status,error_json FROM agent_tasks WHERE group_id=?',(group,))]
    assert all(r['status']=='succeeded' for r in rows),rows
    assert resume.tick()==[{'group_id':group,'state':'preparing_candidates'}]
    value=client.get(base+'/resume-policy').json()
    assert value['state']=='enabled' and value['candidate_handoff']['task_offset']==5
    assert resume.tick()==[{'group_id':group,'state':'completed'}]
    assert client.get(base+'/resume-policy').json()['candidate_handoff']['task_offset']==6
    assert resume.tick()==[]


def test_preexisting_manual_candidate_is_not_relabelled_as_automatic(client,monkeypatch):
    import research_candidate_handoff as handoff
    group,base,path=pending(client,monkeypatch);approve(client,base)
    resume.tick();wait_group(group)
    with core.connect() as db:
        task=db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone()
        claim=core.load(task['result_json'])['claim_ids'][0]
        plan=handoff.plan(db,task['id'],claim)
    saved=handoff.create(task['id'],claim,plan['proposal']['target'],plan['preview_hash']);assert saved['created']
    resume.tick()
    value=client.get(base+'/resume-policy').json()['candidate_handoff']
    assert value['candidate_ids']==[] and value['existing_candidates']==1
    with core.connect() as db:
        event=core.load(db.execute("SELECT payload FROM run_events_v2 WHERE kind='research.candidate_reviewed'").fetchone()[0])
        assert event['approval_source']=='manual_review' and event['policy_revision'] is None


@pytest.mark.parametrize('change',['body','status','missing_hash','omitted_claim'])
def test_saved_draft_edits_require_manual_review_before_automatic_handoff(client,monkeypatch,change):
    group,base,path=pending(client,monkeypatch);approve(client,base)
    resume.tick();wait_group(group)
    with core.connect() as db:
        task=db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone()
        result=core.load(task['result_json']);claim=result['claim_ids'][0]
        assert result['claim_hashes'][claim]
        if change=='body':db.execute("UPDATE research_nodes SET body='Edited after model completion' WHERE id=?",(claim,))
        elif change=='status':db.execute("UPDATE research_nodes SET status='archived' WHERE id=?",(claim,))
        else:
            if change=='missing_hash':result.pop('claim_hashes')
            else:result['claim_ids']=[]
            db.execute('UPDATE agent_tasks SET result_json=? WHERE id=?',(core.dump(result),task['id']))
        calls=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]
    assert resume.tick()==[{'group_id':group,'state':'blocked'}]
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==calls


def test_surface_only_support_stays_a_draft_instead_of_an_automatic_candidate(client,monkeypatch):
    group,base,path=pending(client,monkeypatch,information_only=True);approve(client,base)
    resume.tick();wait_group(group);resume.tick()
    policy=client.get(base+'/resume-policy').json();stage=policy['candidate_handoff']
    assert policy['state']=='completed' and stage['state']=='observations_only'
    assert stage['candidate_ids']==[] and stage['non_security_drafts']==1
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
        task=db.execute('SELECT result_json FROM agent_tasks WHERE group_id=?',(group,)).fetchone()
        claim=core.load(task['result_json'])['claim_ids'][0]
        assert db.execute('SELECT status FROM research_nodes WHERE id=?',(claim,)).fetchone()[0]=='draft'
