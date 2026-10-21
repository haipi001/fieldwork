"""One reviewed, atomic group batch; no model, target access or verification."""
from fastapi import HTTPException
import final_core as core
import research_candidate_handoff as single
from campaign_materials import access


def group_context(db,group_id):
    group=db.execute('SELECT * FROM research_groups WHERE id=?',(group_id,)).fetchone()
    if not group:raise HTTPException(404,'Research group not found')
    campaign=db.execute('SELECT * FROM research_campaigns WHERE id=?',(group['campaign_id'],)).fetchone()
    run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(group['run_id'],)).fetchone()
    if (group['status']!='active' or group['strategy']!='bounded_research' or not campaign or campaign['status']!='active'
        or not run or run['synthetic'] or run['mode']!='traditional' or campaign['engagement_id']!=run['engagement_id']):
        raise HTTPException(409,'Batch handoff requires an active Traditional research group and its real Run')
    return dict(group)


def options(group_id,offset):
    with access(),core.connect() as db:
        db.execute('BEGIN');group=group_context(db,group_id)
        rows=db.execute("SELECT id,result_json FROM agent_tasks WHERE group_id=? AND run_id=? AND campaign_id=? AND status='succeeded' ORDER BY id LIMIT 6 OFFSET ?",
            (group_id,group['run_id'],group['campaign_id'],offset)).fetchall()
        items=[]
        for row in rows[:5]:
            result=core.load(row['result_json'],{})
            if not isinstance(result,dict) or result.get('status')!='research_draft':continue
            claims=result.get('claim_ids',[])
            if not isinstance(claims,list) or len(claims)>5 or any(not isinstance(x,str) for x in claims) or len(set(claims))!=len(claims):
                raise HTTPException(409,'Saved research draft references changed')
            for claim_id in claims:
                node=db.execute("SELECT id,title FROM research_nodes WHERE id=? AND run_id=? AND campaign_id=? AND node_type='claim'",(claim_id,group['run_id'],group['campaign_id'])).fetchone()
                if node:items.append({'task_id':row['id'],'claim_id':node['id'],'title':node['title']})
        return {'group_id':group_id,'run_id':group['run_id'],'items':items,'offset':offset,
            'next_offset':offset+5 if len(rows)>5 else None,'max_selected':3,'source_verification':'not_checked_until_plan','executes_tools':False}


def plan(db,group_id,items):
    group=group_context(db,group_id)
    keys=[(item['task_id'],item['claim_id']) for item in items]
    if not 1<=len(items)<=3 or len(keys)!=len(set(keys)):raise HTTPException(422,'Select one to three distinct draft claims')
    plans=[]
    for item in items:
        task=db.execute('SELECT group_id,run_id,campaign_id FROM agent_tasks WHERE id=?',(item['task_id'],)).fetchone()
        if not task or any(task[k]!=group[k] for k in ('run_id','campaign_id')) or task['group_id']!=group_id:
            raise HTTPException(409,'Selected task does not belong to this group and Run')
        plans.append(single.public_plan(single.plan(db,item['task_id'],item['claim_id'],item.get('target'))))
    return {'group_id':group_id,'run_id':group['run_id'],'items':plans,
        'preview_hash':single.sha({'group':group,'items':plans}), 'can_create':all(item['can_create'] for item in plans),
        'executes_tools':False,'independently_verified':False,'finding_issued':False,'atomic':True}


def preview(group_id,items):
    with access(),core.connect() as db:
        db.execute('BEGIN');return plan(db,group_id,items)


def create(group_id,items,preview_hash):
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE');reviewed=plan(db,group_id,items)
        if not reviewed['can_create'] or reviewed['preview_hash']!=preview_hash:
            raise HTTPException(409,'Batch proposal or sources changed; review again')
        results=[single.create_in_transaction(db,item['task_id'],item['claim_id'],item['proposal']['target'],item['preview_hash']) for item in reviewed['items']]
        if plan(db,group_id,items)['preview_hash']!=preview_hash:raise HTTPException(409,'Batch sources changed during save; no candidates saved')
        return {'group_id':group_id,'run_id':reviewed['run_id'],'items':results,'atomic':True,
            'executes_tools':False,'independently_verified':False,'finding_issued':False}


def prepare(group_id,offset):
    """Prepare a bounded, deterministic review proposal; never save or execute."""
    page=options(group_id,offset)
    selected=[];skipped=[]
    with access(),core.connect() as db:
        db.execute('BEGIN');group_context(db,group_id)
        for row in page['items']:
            if len(selected)==3:break
            key={'task_id':row['task_id'],'claim_id':row['claim_id']}
            try:
                value=plan(db,group_id,[key])['items'][0]
            except HTTPException:
                skipped.append(key|{'reason':'sources_or_authority_require_review'});continue
            if not value['can_create'] or len(value['targets'])!=1:
                skipped.append(key|{'reason':'target_requires_manual_selection'});continue
            if db.execute('SELECT 1 FROM candidate_findings WHERE id=?',(value['candidate_id'],)).fetchone():
                skipped.append(key|{'reason':'candidate_already_saved'});continue
            selected.append(key|{'target':value['proposal']['target']})
        reviewed=plan(db,group_id,selected) if selected else None
        return {'group_id':group_id,'run_id':page['run_id'],'offset':offset,'next_offset':page['next_offset'],
            'selection':selected,'plan':reviewed,'skipped':skipped,
            'not_examined':len(page['items'])-len(selected)-len(skipped),
            'selection_order':'saved_task_id_then_claim_order','executes_tools':False,'saves_candidates':False}
