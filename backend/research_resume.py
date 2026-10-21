"""Explicit durable permission to dispatch only an exact set of unstarted tasks."""
import hashlib
import json
import uuid
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException
import final_core as core
from campaign_materials import access

PREFIX='research-resume:'
MAX_TASKS=1000


def sha(value):return hashlib.sha256(core.dump(value).encode()).hexdigest()


def read(db,group):
    row=db.execute('SELECT value FROM app_metadata WHERE key=?',(PREFIX+group,)).fetchone()
    return core.load(row['value']) if row else None


def write(db,group,value):
    db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
        (PREFIX+group,core.dump(value),core.utcnow()))


def task_hash(task):
    return sha({k:task[k] for k in ('id','campaign_id','run_id','group_id','role','objective','context_capsule_json','tool_grants_json','route_requirement_json','budget_json','max_attempts')})


def binding(db,group):
    import v5_runtime as runtime
    row=db.execute('SELECT * FROM research_groups WHERE id=?',(group,)).fetchone()
    if not row or row['status']!='active' or row['strategy']!='bounded_research':raise HTTPException(409,'Active bounded research group required')
    profile=db.execute('SELECT * FROM runtime_profiles WHERE id=?',(row['runtime_profile_id'],)).fetchone()
    if not profile:raise HTTPException(409,'Automatic resume requires an explicit runtime Profile on this group')
    config=core.load(profile['config_json'])
    ids=set(config.get('local_provider_ids',[])+config.get('cloud_provider_ids',[])+config.get('independent_provider_ids',[]))
    providers={}
    for provider in db.execute('SELECT * FROM runtime_providers'):
        if provider['id'] in ids:providers[provider['id']]=runtime._provider_config_hash(provider)
    if set(providers)!=ids or not providers:raise HTTPException(409,'Configured model providers are unavailable')
    return {'group':sha({k:row[k] for k in ('id','campaign_id','run_id','objective','strategy','runtime_profile_id','budget_json')}),
        'profile':sha(dict(profile)),'providers':providers}


def plan_in_transaction(db,group,candidate_limit=0):
    import v5_research_worker as worker
    bound=binding(db,group)
    tasks=db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt=0 ORDER BY id LIMIT ?",(group,MAX_TASKS+1)).fetchall()
    if not tasks or len(tasks)>MAX_TASKS:raise HTTPException(409,'Select a group with 1 to 1000 unstarted queued tasks')
    hashes={}
    for task in tasks:
        if task['role'] not in {'researcher','explorer','specialist'} or not core.load(task['context_capsule_json']).get('team_plan'):raise HTTPException(409,'Automatic resume supports only configured research-team tasks')
        if not core.load(task['context_capsule_json']).get('research_input_hashes'):
            raise HTTPException(409,'Freeze research inputs with the existing explicit start flow before enabling resume')
        if db.execute('SELECT 1 FROM runtime_calls WHERE task_id=?',(task['id'],)).fetchone():raise HTTPException(409,'Unstarted resume tasks must have no model-call history')
        worker._inputs(db,task);hashes[task['id']]=task_hash(task)
    authority=None
    if candidate_limit:
        from research_auto_candidates import approve_inputs
        authority=approve_inputs(db,group,tasks)
    prior=read(db,group)
    proposal={'group_id':group,'run_id':tasks[0]['run_id'],'binding':bound,'task_hashes':hashes,
        'expected_revision':prior['revision'] if prior else None,'candidate_limit':candidate_limit,'candidate_authority':authority,
        'operation':'resume_unstarted_recorded_graph_tasks','app_must_be_running':True,
        'replays_started_tasks':False,'starts_target_tools':False,'promotes_findings':False}
    return proposal|{'preview_hash':sha(proposal),'task_count':len(tasks)}


def preview(group,candidate_limit=0):
    with access(),core.connect() as db:
        db.execute('BEGIN');return plan_in_transaction(db,group,candidate_limit)


def public(value):
    if not value:return {'configured':False,'state':'disabled','revision':None,'task_count':0}
    return {'configured':True,'state':value['state'],'revision':value['revision'],
        'task_count':len(value['task_hashes']),'reason':value.get('reason'),'candidate_limit':value.get('candidate_limit',0),
        'candidate_handoff':value.get('candidate_handoff'),
        'approved_at':value['approved_at'],'app_must_be_running':True,
        'replays_started_tasks':False,'starts_target_tools':False,'promotes_findings':False}


def mark_scheduler(state,error_type=None):
    import v5_research_worker as worker
    with core.connect() as db:
        value={'state':state,'runner_id':worker.RUNNER_ID,'last_tick_at':core.utcnow(),'error_type':error_type}
        db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
            ('research_resume_scheduler',core.dump(value),core.utcnow()))


def scheduler_status(db):
    import v5_research_worker as worker
    row=db.execute("SELECT value FROM app_metadata WHERE key='research_resume_scheduler'").fetchone()
    if not row:return {'state':'unobserved','last_tick_at':None,'error_type':None}
    value=core.load(row['value'])
    fresh=value['last_tick_at']>(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()
    state=value['state'] if value['state']=='stopped' or (fresh and value['runner_id']==worker.RUNNER_ID) else 'stale'
    return {'state':state,'last_tick_at':value['last_tick_at'],'error_type':value['error_type']}


def progress(db,group,value):
    counts={'unstarted':0,'running':0,'drafts_saved':0,'review_required':0,'missing':0}
    if not value:return counts
    ids=list(value['task_hashes'])
    rows=db.execute('SELECT status,attempt,result_json FROM agent_tasks WHERE group_id=? AND id IN (SELECT value FROM json_each(?))',(group,core.dump(ids))).fetchall()
    counts['missing']=len(ids)-len(rows)
    for row in rows:counts[task_progress_key(row)]+=1
    return counts



def task_progress_key(row):
    result=core.load(row['result_json'],{})
    if row['status']=='queued' and row['attempt']==0:return 'unstarted'
    if row['status'] in {'leased','running'}:return 'running'
    if row['status'] in {'succeeded','completed'} and isinstance(result,dict) and result.get('status')=='research_draft':return 'drafts_saved'
    return 'review_required'


def review_tasks(db,group,value):
    if not value:return {'items':[],'remaining':0}
    rows=db.execute('SELECT * FROM agent_tasks WHERE group_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY id',(group,core.dump(list(value['task_hashes'])))).fetchall()
    pending=[row for row in rows if task_progress_key(row)=='review_required']
    items=[]
    codes={'model_call_interrupted_review','model_output_invalid_usage_settled','model_usage_unknown','model_budget_blocked','research_context_too_large','research_input_stale','research_worker_rejected','research_worker_stopped'}
    from v5_model_response import REJECTION_REASONS,FINISH_REASONS
    for row in pending[:5]:
        error=core.load(row['error_json'],{});error=error if isinstance(error,dict) else {}
        code=error.get('code');reason=error.get('model_output_reason');finish=error.get('model_finish_reason')
        ledger={item['state']:item['count'] for item in db.execute('SELECT state,count(*) count FROM runtime_calls WHERE task_id=? GROUP BY state',(row['id'],)) if item['state'] in {'reserved','calling','unknown','settled','released'}}
        items.append({'id':row['id'],'status':row['status'],'attempt':row['attempt'],
            'error_code':code if isinstance(code,str) and code in codes else None,
            'model_output_reason':reason if isinstance(reason,str) and reason in REJECTION_REASONS|{'schema_mismatch'} else None,
            'model_finish_reason':finish if isinstance(finish,str) and finish in FINISH_REASONS else None,
            'call_counts':ledger,'automatic_retry':False})
    return {'items':items,'remaining':max(0,len(pending)-len(items))}


def status(group):
    with access(),core.connect() as db:
        if not db.execute('SELECT 1 FROM research_groups WHERE id=?',(group,)).fetchone():raise HTTPException(404,'Research group not found')
        value=read(db,group);counts=progress(db,group,value);result=public(value)
        if value and value['state']=='completed' and counts['drafts_saved']!=len(value['task_hashes']):
            result.update(state='blocked',stored_state='completed',reason='completed_policy_tasks_require_review')
        return result|{'scheduler':scheduler_status(db),'task_progress':counts,'review_tasks':review_tasks(db,group,value)}


def enable(group,preview_hash,candidate_limit=0):
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE');plan=plan_in_transaction(db,group,candidate_limit)
        if plan['preview_hash']!=preview_hash:raise HTTPException(409,'Resume proposal changed; review again')
        value={k:plan[k] for k in ('binding','task_hashes','candidate_limit','candidate_authority')}
        value.update(state='enabled',revision=uuid.uuid4().hex,approved_at=core.utcnow())
        write(db,group,value)
        import v5_runtime as runtime
        runtime._emit(db,group,'team_resume.enabled',{'task_count':len(value['task_hashes']),'revision':value['revision']})
        return public(value)


def disable(group,revision):
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE');value=read(db,group)
        if not value or value['revision']!=revision:raise HTTPException(409,'Resume policy changed; reload')
        value.update(state='disabled',revision=uuid.uuid4().hex)
        write(db,group,value)
        import v5_runtime as runtime
        runtime._emit(db,group,'team_resume.disabled',{'inflight_may_finish':True})
        return public(value)


def validate(db,group,revision,task=None):
    value=read(db,group)
    if not value or value['state']!='enabled' or value['revision']!=revision:raise HTTPException(409,'Resume policy is no longer enabled')
    if binding(db,group)!=value['binding']:raise HTTPException(409,'Resume group or model configuration changed')
    if value.get('candidate_limit'):
        from research_auto_candidates import authority
        if authority(db,group)!=value.get('candidate_authority'):raise HTTPException(409,'Automatic candidate authority changed')
    counts=progress(db,group,value)
    if counts['missing']:raise HTTPException(409,'Approved task inventory changed')
    if counts['review_required']:raise HTTPException(409,'An approved task requires review')
    if task is not None:
        if task['id'] not in value['task_hashes'] or task['attempt']!=0 or task['status']!='queued' or task_hash(task)!=value['task_hashes'][task['id']]:raise HTTPException(409,'Resume task changed or already started')
        if db.execute('SELECT 1 FROM runtime_calls WHERE task_id=?',(task['id'],)).fetchone():raise HTTPException(409,'Resume task has model-call history')
    return value


def tick():
    """One bounded scheduler pass. No failed/started task is ever retried here."""
    import v5_research_worker as worker
    from v5_orchestration import recover_expired_leases
    recover_expired_leases()
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cursor=db.execute("SELECT value FROM app_metadata WHERE key='research_resume_cursor'").fetchone()
        last=cursor['value'] if cursor else ''
        keys=[row['key'] for row in db.execute("SELECT key FROM app_metadata WHERE key LIKE ? AND key>? AND json_extract(value,'$.state')='enabled' ORDER BY key LIMIT 8",(PREFIX+'%',last))]
        if len(keys)<8:
            keys += [row['key'] for row in db.execute("SELECT key FROM app_metadata WHERE key LIKE ? AND key<=? AND json_extract(value,'$.state')='enabled' ORDER BY key LIMIT ?",(PREFIX+'%',last,8-len(keys)))]
        if keys:
            db.execute("INSERT INTO app_metadata VALUES('research_resume_cursor',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",(keys[-1],core.utcnow()))
        groups=[key[len(PREFIX):] for key in keys]
    results=[]
    for group in groups:
        revision=None
        try:
            with worker._lock:
                if group in worker._jobs and worker._jobs[group].is_alive():
                    results.append({'group_id':group,'state':'running'});continue
            with core.connect() as db:
                db.execute('BEGIN IMMEDIATE');value=read(db,group)
                if not value or value['state']!='enabled':continue
                revision=value['revision'];validate(db,group,revision)
                if worker._admission(db)['state']!='accepting':
                    results.append({'group_id':group,'state':'waiting_admission'});continue
                tasks=db.execute("SELECT * FROM agent_tasks WHERE id IN (SELECT value FROM json_each(?)) AND status='queued' AND attempt=0 ORDER BY id",(core.dump(list(value['task_hashes'])),)).fetchall()
                if not tasks and db.execute("SELECT 1 FROM agent_tasks WHERE id IN (SELECT value FROM json_each(?)) AND status IN ('leased','running') LIMIT 1",(core.dump(list(value['task_hashes'])),)).fetchone():
                    results.append({'group_id':group,'state':'waiting_executor'});continue
                if not tasks:
                    from research_auto_candidates import finish
                    done=finish(db,group,value)
                    value['state']='completed' if done else 'enabled';write(db,group,value)
                    results.append({'group_id':group,'state':'completed' if done else 'preparing_candidates'});continue
                for task in tasks:
                    if task_hash(task)!=value['task_hashes'][task['id']] or db.execute('SELECT 1 FROM runtime_calls WHERE task_id=?',(task['id'],)).fetchone():raise HTTPException(409,'Approved unstarted task changed')
                worker._inputs(db,tasks[0])
            execution=worker.group_execution(group)
            if execution['state']=='active_marker' or execution['unexpired_leases']:
                results.append({'group_id':group,'state':'waiting_executor'});continue
            result=worker._start_group_research(group,resume_revision=revision)
            results.append({'group_id':group,'state':result['status']})
        except (HTTPException,ValueError,TypeError) as error:
            with core.connect() as db:
                db.execute('BEGIN IMMEDIATE');value=read(db,group)
                if value and value['state']=='enabled' and value['revision']==revision:
                    value.update(state='blocked',reason='current_configuration_inputs_or_execution_require_review')
                    write(db,group,value)
            results.append({'group_id':group,'state':'blocked'})
    return results


def scheduler_pass():
    try:
        result=tick()
    except Exception as error:
        mark_scheduler('error',type(error).__name__)
        return []
    mark_scheduler('running')
    return result
