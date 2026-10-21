"""Separately approved, bounded draft handoff. Never executes or verifies a target."""
from fastapi import HTTPException
import final_core as core
from research_batch_handoff import group_context
from research_candidate_handoff import sha


def authority(db,group):
    row=group_context(db,group)
    value=db.execute('''SELECT r.scope_snapshot_id,r.policy_id,e.current_scope_snapshot_id,e.current_policy_id,e.status,s.confirmed_at,s.rules,p.policy
        FROM analysis_runs r JOIN engagements_v2 e ON e.id=r.engagement_id
        JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
        JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id WHERE r.id=?''',(row['run_id'],)).fetchone()
    if (not value or value['status']!='ready' or not value['confirmed_at']
        or value['scope_snapshot_id']!=value['current_scope_snapshot_id'] or value['policy_id']!=value['current_policy_id']):
        raise HTTPException(409,'Automatic candidates require current confirmed Traditional authority')
    return sha(dict(value))


def approve_inputs(db,group,tasks):
    import v5_research_worker as worker
    from research_material_review import source_status
    frozen=authority(db,group)
    budget={'remaining':8000000,'checked':{}}
    for task in tasks:
        for node in worker._inputs(db,task):
            if source_status(db,node,task['run_id'],budget)!='verified':
                raise HTTPException(409,'Automatic candidates require verified registered input sources')
    return frozen


def finish(db,group,value):
    """Five exact approved tasks per tick. Cursor and candidate writes commit together."""
    import research_candidate_handoff as handoff
    import v5_workers as workers
    from research_resume import task_hash
    limit=value.get('candidate_limit',0)
    if not limit:return True
    if authority(db,group)!=value.get('candidate_authority'):
        raise HTTPException(409,'Automatic candidate authority changed')
    state=value.get('candidate_handoff') or {'state':'preparing','task_offset':0,'candidate_ids':[],'review_required':0,'run_id':None}
    ids=sorted(value['task_hashes'])
    for task_id in ids[state['task_offset']:state['task_offset']+5]:
        task=db.execute('SELECT * FROM agent_tasks WHERE id=? AND group_id=?',(task_id,group)).fetchone()
        if not task or task_hash(task)!=value['task_hashes'][task_id]:raise HTTPException(409,'Approved unstarted task changed')
        result=core.load(task['result_json'],{})
        claims=result.get('claim_ids',[])
        if not isinstance(claims,list) or len(claims)>5 or any(not isinstance(c,str) for c in claims) or len(claims)!=len(set(claims)):
            raise HTTPException(409,'Saved research draft references changed')
        hashes=result.get('claim_hashes')
        if not isinstance(hashes,dict) or set(hashes)!=set(claims):raise HTTPException(409,'Saved draft hashes are missing or changed; review manually')
        for claim in claims:
            node=db.execute('SELECT * FROM research_nodes WHERE id=?',(claim,)).fetchone()
            if not node or node['status']!='draft' or workers._node_digest(node)!=hashes[claim]:
                raise HTTPException(409,'Saved draft content changed; review manually')
        state['run_id']=task['run_id']
        for claim in claims:
            plan=handoff.plan(db,task_id,claim)
            if plan['proposal']['category']=='research_hypothesis':
                from candidate_quality import security_category
                if any(security_category(row) for row in plan['_observations'].values()):state['review_required']+=1
                else:state['non_security_drafts']=state.get('non_security_drafts',0)+1
                continue
            if not plan['can_create'] or len(plan['targets'])!=1:
                state['review_required']+=1;continue
            if db.execute('SELECT 1 FROM candidate_findings WHERE id=?',(plan['candidate_id'],)).fetchone():
                state['existing_candidates']=state.get('existing_candidates',0)+1;continue
            saved=handoff.create_in_transaction(db,task_id,claim,plan['proposal']['target'],plan['preview_hash'],policy_revision=value['revision'])
            if saved['id'] not in state['candidate_ids']:state['candidate_ids'].append(saved['id'])
            if len(state['candidate_ids'])>=limit:break
        state['task_offset']+=1
        if len(state['candidate_ids'])>=limit:break
    done=state['task_offset']>=len(ids) or len(state['candidate_ids'])>=limit
    if len(state['candidate_ids'])>=limit:state['state']='limit_reached'
    elif done:state['state']='needs_review' if state['review_required'] else 'observations_only' if state.get('non_security_drafts') and not state['candidate_ids'] else 'completed'
    else:state['state']='preparing'
    value['candidate_handoff']=state
    return done
