"""Explicit, bounded local adapter dispatch. No automatic replay or Finding promotion."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
import final_core as core
from agent_attack_adapters import current as adapter_current
from agent_attack_requests import Preview,preview as request_preview,digest
from agent_attack_executor import SCENARIOS,TOOL_PROTOCOL,PROTOCOL
from storage_access import storage_access,open_runtime_lock
from storage_migration import _open_directory,_plain_directory

router=APIRouter(prefix='/agent-attack-requests',tags=['Local adapter dispatch'])

class Plan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    engagement_id:str=Field(min_length=1,max_length=200)
    timeout_seconds:int=Field(default=5,strict=True,ge=1,le=15)
    authorization_note:str=Field(min_length=3,max_length=1000)

class Execute(Plan):
    plan_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:StrictBool


def row_request(db,request_id):
    row=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(request_id,)).fetchone()
    if not row or row['mode']!='reviewed_request':raise HTTPException(404,'Attack request not found')
    return dict(row)


def plan(db,request_id,body):
    row=row_request(db,request_id)
    if row['status'] not in ('awaiting_adapter','awaiting_execution_review'):raise HTTPException(409,'Request is no longer awaiting execution review')
    saved=core.load(row['result_json'],{})
    inputs=Preview(**{k:saved[k] for k in ('agent_id','category','max_cases')})
    fresh=request_preview(db,inputs)
    if fresh['preview_hash']!=saved['preview_hash']:raise HTTPException(409,'Agent or adapter changed; create a freshly reviewed request')
    adapter=adapter_current(db,row['agent_id'])
    if not adapter or not adapter['enabled']:raise HTTPException(409,'A reviewed enabled local adapter is required')
    if fresh['agent_status'] not in ('registered','active','ready'):raise HTTPException(409,'Agent identity is not enabled for execution')
    if row['scenario_id'] not in SCENARIOS or saved['max_cases']<2:raise HTTPException(409,'Only supported two-case local synthetic scenarios are executable')
    if sys.platform!='darwin' or not Path('/usr/bin/sandbox-exec').is_file():raise HTTPException(409,'macOS sandbox is required')
    project=db.execute('SELECT * FROM engagements_v2 WHERE id=?',(body.engagement_id,)).fetchone()
    if not project or project['mode']!='agent_audit' or project['status']!='ready':raise HTTPException(409,'Select a ready Agent Audit project')
    scope=db.execute('SELECT * FROM scope_snapshots WHERE id=? AND engagement_id=? AND confirmed_at IS NOT NULL',(project['current_scope_snapshot_id'],project['id'])).fetchone()
    policy=db.execute('SELECT * FROM execution_policies WHERE id=? AND engagement_id=?',(project['current_policy_id'],project['id'])).fetchone()
    if not scope or not policy:raise HTTPException(409,'Current project authorization cannot be verified')
    value={'request_id':request_id,'request_preview_hash':saved['preview_hash'],**body.model_dump(),
        'agent_id':row['agent_id'],'agent_snapshot_sha256':fresh['agent_snapshot_sha256'],
        'component_snapshot_sha256':fresh['component_snapshot_sha256'],'adapter_sha256':adapter['row']['sha256'],
        'adapter_configuration_sha256':adapter['configuration_sha256'],
        'current_scope_snapshot_id':scope['id'],'current_policy_id':policy['id'],
        'scope_sha256':digest(dict(scope)),'policy_sha256':digest(dict(policy)),
        'project_sha256':digest(dict(project)),'scenario_id':SCENARIOS[row['scenario_id']],'adapter_protocol':TOOL_PROTOCOL if row['scenario_id']=='tool' else PROTOCOL,
        'case_count':2,'adapter_invocation_limit':2,'tool_call_reservation':2,'max_execution_seconds':2*body.timeout_seconds,
        'network_requests':0,'model_budget_micros':0,
        'tool_permissions':[{'name':'fixture.read','allowed_resources':['fixture://public'],'max_calls_per_case':4,'synthetic_only':True}] if row['scenario_id']=='tool' else [],
        'host_tool_permissions':[],'max_synthetic_broker_calls':8 if row['scenario_id']=='tool' else 0,
        'new_scope_action':'local_synthetic_adapter_test','synthetic':True,
        'new_run':True,'automatic_replay':False,'finding_promotion':False}
    value['plan_hash']=digest(value)
    return value


@contextmanager
def execution_lock():
    parent=_open_directory(_plain_directory(core.LOCAL_DATA_ROOT));descriptor=None
    try:
        descriptor=open_runtime_lock(parent,core.LOCAL_DATA_ROOT,'.agent-attack-execution.lock')
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)&0o077:raise HTTPException(409,'Invalid execution lock')
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise HTTPException(409,'A local adapter execution is already active') from None
        current=os.stat('.agent-attack-execution.lock',dir_fd=parent,follow_symlinks=False)
        if (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):raise HTTPException(409,'Execution lock changed')
        yield
    finally:
        if descriptor is not None:os.close(descriptor)
        os.close(parent)


@router.post('/{request_id}/execution-plan')
def read_plan(request_id:str,body:Plan):
    with core.connect() as db:
        db.execute('BEGIN')
        return plan(db,request_id,body)


@router.get('/{request_id}/execution')
def read_execution(request_id:str):
    with core.connect() as db:
        row=row_request(db,request_id);value=core.load(row['result_json'],{})
        return {'id':request_id,'status':row['status'],'execution':value.get('execution'),
            'evidence_ids':core.load(row['evidence_json'],[]),'finding_issued':False}


@router.post('/{request_id}/execution-cancel')
def cancel_execution(request_id:str):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE');row=row_request(db,request_id)
        if row['status'] not in ('executing','cancelling'):raise HTTPException(409,'No active execution to cancel')
        if row['status']!='cancelling':
            db.execute("UPDATE agent_attack_tests_v5 SET status='cancelling' WHERE id=?",(request_id,))
            db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',('agent_attack',None,request_id,'agent_attack.execution_cancel_requested','{}',core.utcnow()))
        return {'id':request_id,'status':'cancelling'}


@router.post('/{request_id}/recover-interrupted')
def recover(request_id:str):
    with storage_access(core.LOCAL_DATA_ROOT),execution_lock(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE');row=row_request(db,request_id)
        if row['status'] not in ('executing','cancelling'):raise HTTPException(409,'Request does not need interrupted recovery')
        value=core.load(row['result_json']);value['execution']['interruption']='executor_no_longer_holds_lock; no automatic replay'
        budget=db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(value['execution']['run_id'],)).fetchone()
        value['execution']['adapter_invocations_used']=budget[0] if budget else None
        db.execute("UPDATE agent_attack_tests_v5 SET status='interrupted',result_json=?,completed_at=? WHERE id=?",(core.dump(value),core.utcnow(),request_id))
        db.execute("UPDATE analysis_runs SET status='interrupted' WHERE id=?",(value['execution']['run_id'],))
        db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',('agent_attack',None,request_id,'agent_attack.execution_interrupted',core.dump({'run_id':value['execution']['run_id'],'automatic_replay':False}),core.utcnow()))
        return {'id':request_id,'status':'interrupted','automatic_replay':False}


def stopped(request_id,approved,scope_id):
    try:
        with core.connect() as db:
            row=row_request(db,request_id)
            project=db.execute('SELECT current_scope_snapshot_id,current_policy_id,status FROM engagements_v2 WHERE id=?',(approved['engagement_id'],)).fetchone()
            scope=db.execute('SELECT * FROM scope_snapshots WHERE id=?',(scope_id,)).fetchone()
            policy=db.execute('SELECT * FROM execution_policies WHERE id=?',(approved['current_policy_id'],)).fetchone()
            adapter=adapter_current(db,approved['agent_id'])
            saved=core.load(row['result_json'],{}).get('execution',{})
            run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(saved.get('run_id'),)).fetchone()
            budget=db.execute('SELECT * FROM run_budgets_v2 WHERE run_id=?',(saved.get('run_id'),)).fetchone()
            agent=db.execute('SELECT * FROM agent_registry_v5 WHERE id=?',(approved['agent_id'],)).fetchone()
            components=[dict(x) for x in db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=? ORDER BY id',(approved['agent_id'],))]
            return (not run or run['status']!='running' or run['scope_snapshot_id']!=scope_id
                or not budget or budget['tool_call_limit']!=2 or not 0<=budget['tool_calls_used']<=2
                or budget['request_limit']!=0 or budget['requests_used']!=0 or budget['model_budget_micros']!=0 or budget['model_cost_micros']!=0
                or not agent or digest(dict(agent))!=approved['agent_snapshot_sha256']
                or digest(components)!=approved['component_snapshot_sha256']
                or row['status']!='executing' or not project or project['status']!='ready'
                or project['current_scope_snapshot_id']!=scope_id or project['current_policy_id']!=approved['current_policy_id']
                or not scope or digest(dict(scope))!=approved['execution_scope_sha256']
                or not policy or digest(dict(policy))!=approved['policy_sha256']
                or not adapter or adapter['configuration_sha256']!=approved['adapter_configuration_sha256'])
    except Exception:return True


@router.post('/{request_id}/execute')
def execute(request_id:str,body:Execute):
    if not body.confirmed:raise HTTPException(422,'Confirm the exact local synthetic execution and new scope')
    inputs=Plan(**body.model_dump(include={'engagement_id','timeout_seconds','authorization_note'}))
    with storage_access(core.LOCAL_DATA_ROOT),execution_lock():
        with core.connect() as db:
            db.execute('BEGIN IMMEDIATE');approved=plan(db,request_id,inputs)
            if approved['plan_hash']!=body.plan_hash:raise HTTPException(409,'Execution plan changed; review again')
            row=row_request(db,request_id);value=core.load(row['result_json'])
            adapter=adapter_current(db,row['agent_id']);source=adapter['source_code']
            old=db.execute('SELECT * FROM scope_snapshots WHERE id=?',(approved['current_scope_snapshot_id'],)).fetchone()
            rules=core.load(old['rules'],{});rules['allowed_actions']=list(dict.fromkeys([*rules.get('allowed_actions',[]),'local_synthetic_adapter_test']));rules['local_synthetic_adapter_test']={'agent_id':row['agent_id'],'source_sha256':approved['adapter_sha256'],'network_allowed':False,'tool_permissions':approved['tool_permissions'],'scenario_id':approved['scenario_id'],'case_count':2,'timeout_seconds_per_case':body.timeout_seconds,'authorization_note':body.authorization_note}
            scope_id=core.uid('scope');run_id=core.uid('run');now=core.utcnow()
            version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM scope_snapshots WHERE engagement_id=?',(body.engagement_id,)).fetchone()[0]
            db.execute('INSERT INTO scope_snapshots VALUES(?,?,?,?,?,?,?,?)',(scope_id,body.engagement_id,version,'agent_audit',core.dump(rules),'operator-confirmed local synthetic adapter plan',now,now))
            db.execute('UPDATE engagements_v2 SET current_scope_snapshot_id=?,updated_at=? WHERE id=?',(scope_id,now,body.engagement_id))
            db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,body.engagement_id,'agent_audit',scope_id,approved['current_policy_id'],'running','local_adapter_test',1,None,None,None,None,now))
            db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run_id,0,0,2,0,0,0,now))
            approved['execution_scope_sha256']=digest(dict(db.execute('SELECT * FROM scope_snapshots WHERE id=?',(scope_id,)).fetchone()))
            value['execution']={'plan':approved,'run_id':run_id,'scope_snapshot_id':scope_id,'reserved_cases':2,'started_at':now}
            db.execute("UPDATE agent_attack_tests_v5 SET status='executing',result_json=? WHERE id=?",(core.dump(value),request_id))
            db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',('agent_attack',None,request_id,'agent_attack.execution_started',core.dump({'run_id':run_id,'scope_snapshot_id':scope_id,'plan_hash':approved['plan_hash'],'adapter_sha256':approved['adapter_sha256']}),now))
        def charge_case():
            with core.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if stopped(request_id,approved,scope_id):raise RuntimeError('execution authorization changed')
                charged=db.execute('UPDATE run_budgets_v2 SET tool_calls_used=tool_calls_used+1,updated_at=? WHERE run_id=? AND tool_calls_used<tool_call_limit',(core.utcnow(),run_id))
                if charged.rowcount!=1:raise RuntimeError('case budget exhausted')
        def save_checkpoint(event):
            from agent_tool_checkpoints import persist
            return persist(request_id,event,lambda:not stopped(request_id,approved,scope_id))
        result=None;error=None
        try:
            from agent_attack_executor import execute_local_adapter
            result=execute_local_adapter(source,expected_sha256=approved['adapter_sha256'],timeout_seconds=body.timeout_seconds,
                cancel_requested=lambda:stopped(request_id,approved,scope_id),before_case=charge_case,category=row['scenario_id'],on_tool_checkpoint=save_checkpoint if row['scenario_id']=='tool' else None)
        except Exception as failure:error=type(failure).__name__
        cancelled=stopped(request_id,approved,scope_id)
        status='cancelled' if cancelled else ('observed' if result and result['status']=='observed' else 'incomplete')
        artifact_id=core.uid('artifact');artifact_name=artifact_id+'.json'
        root=core.LOCAL_DATA_ROOT/'agent_adapter_results';root.mkdir(mode=0o700,exist_ok=True)
        directory=_open_directory(_plain_directory(root));created=False
        try:
            with core.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if stopped(request_id,approved,scope_id):status='cancelled'
                row=row_request(db,request_id);value=core.load(row['result_json'])
                budget=db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(run_id,)).fetchone()
                value['execution']['adapter_invocations_used']=budget[0] if budget else None
                payload={'request_id':request_id,'run_id':run_id,'execution_plan':approved,'result':result,'error_type':error,'status':status,'finding_issued':False,
                    'tool_checkpoints':value['execution'].get('tool_checkpoints',[])}
                raw=core.dump(payload).encode();sha=hashlib.sha256(raw).hexdigest()
                fd=os.open(artifact_name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=directory);created=True
                with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                value['execution'].update({'status':status,'artifact_id':artifact_id,'artifact_sha256':sha,'error_type':error,
                    'case_count':result['case_count'] if result else 0,'finding_issued':False,
                    'observation':{'control_clean':result['control_clean'] if result else None,'attack_specific_disclosure':result['attack_specific_disclosure'] if result else None,'attack_specific_memory_mutation':result.get('attack_specific_memory_mutation') if result else None,'attack_specific_forbidden_tool_request':result.get('attack_specific_forbidden_tool_request') if result else None,'assessment_usable':status=='observed'}})
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',(artifact_id,run_id,'agent.local_adapter_observation',str(root/artifact_name),sha,'application/json',0,core.utcnow()))
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(core.uid('obs'),run_id,body.engagement_id,'agent_audit','agent.local_adapter_observation',row['agent_id'],core.dump({'status':status,'case_count':result['case_count'] if result else 0,'scope':result['observation_scope'] if result else 'observation unavailable'}),.5,'local_adapter_supervisor',artifact_id,core.utcnow()))
                db.execute('UPDATE agent_attack_tests_v5 SET status=?,result_json=?,evidence_json=?,completed_at=? WHERE id=?',(status,core.dump(value),core.dump([artifact_id]),core.utcnow(),request_id))
                db.execute('UPDATE analysis_runs SET status=?,current_stage=? WHERE id=?',('cancelled' if status=='cancelled' else ('completed' if status=='observed' else 'failed'),'local_adapter_test',run_id))
                db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',('agent_attack',None,request_id,'agent_attack.execution_finished',core.dump({'run_id':run_id,'status':status,'artifact_id':artifact_id}),core.utcnow()))
        except Exception:
            if created:os.unlink(artifact_name,dir_fd=directory)
            # Leave an explicit running record for lock-based interrupted recovery.
            raise
        finally:os.close(directory)
        return {'id':request_id,'status':status,'run_id':run_id,'artifact_id':artifact_id,'case_count':result['case_count'] if result else 0,
            'known_tool_requests':len(value['execution'].get('tool_checkpoints',[])),'finding_issued':False,'independently_verified':False}

@router.get('/{request_id}/evidence')
def read_evidence(request_id:str):
    from proof_materials import read_proof_file
    with storage_access(core.LOCAL_DATA_ROOT),core.connect() as db:
        db.execute('BEGIN')
        row=row_request(db,request_id);execution=core.load(row['result_json'],{}).get('execution',{})
        artifact=db.execute('SELECT * FROM artifacts WHERE id=? AND run_id=?',(execution.get('artifact_id'),execution.get('run_id'))).fetchone()
        if not artifact or artifact['kind']!='agent.local_adapter_observation':raise HTTPException(409,'No registered local adapter observation is available')
        try:
            path=Path(artifact['uri']);path.relative_to(core.LOCAL_DATA_ROOT)
            raw=read_proof_file(path,2_000_000)
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256'] or artifact['sha256']!=execution['artifact_sha256']:raise ValueError()
            value=json.loads(raw)
            if value['request_id']!=request_id or value['run_id']!=execution['run_id']:raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):raise HTTPException(409,'Registered observation integrity check failed') from None
        approved=value['execution_plan']
        project=db.execute('SELECT * FROM engagements_v2 WHERE id=?',(approved['engagement_id'],)).fetchone()
        scope=db.execute('SELECT * FROM scope_snapshots WHERE id=?',(execution['scope_snapshot_id'],)).fetchone()
        policy=db.execute('SELECT * FROM execution_policies WHERE id=?',(approved['current_policy_id'],)).fetchone()
        try:adapter=adapter_current(db,row['agent_id'])
        except HTTPException:adapter=None
        agent=db.execute('SELECT * FROM agent_registry_v5 WHERE id=?',(row['agent_id'],)).fetchone()
        components=[dict(item) for item in db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=? ORDER BY id',(row['agent_id'],))]
        current=bool(agent and digest(dict(agent))==approved.get('agent_snapshot_sha256')
            and digest(components)==approved.get('component_snapshot_sha256') and project and project['status']=='ready' and project['current_scope_snapshot_id']==execution['scope_snapshot_id']
            and project['current_policy_id']==approved['current_policy_id'] and scope and digest(dict(scope))==approved['execution_scope_sha256']
            and policy and digest(dict(policy))==approved['policy_sha256'] and adapter and adapter['configuration_sha256']==approved['adapter_configuration_sha256'])
        return {'request_id':request_id,'run_id':execution['run_id'],'artifact_id':artifact['id'],
            'sha256':artifact['sha256'],'integrity_verified':True,'current_authorization_and_adapter':current,
            'status':row['status'],'observation':value,'independently_verified':False,'finding_issued':False}


@router.get('/{request_id}/checkpoints')
def read_checkpoints(request_id:str):
    from agent_tool_checkpoints import read
    with storage_access(core.LOCAL_DATA_ROOT):return read(request_id)


@router.get('/{request_id}/review')
def review_observation(request_id:str):
    from agent_attack_review import summarize
    evidence=read_evidence(request_id)
    try:return summarize(evidence)
    except (KeyError,ValueError,TypeError,UnicodeError):raise HTTPException(409,'Observation review fields or digests cannot be verified') from None
