"""Operator workspace views over persisted records. Reads never execute tools."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool
import final_core as f
from reporting import redact_structure

router = APIRouter(prefix='/api/v1/workspace', tags=['Workspace UI'])


class ExtensionPackage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    package_base64: str = Field(min_length=1,max_length=5592408)
    expected_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ExtensionInstall(ExtensionPackage):
    expected_current_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool


class ExtensionRestore(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_current_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool


@router.get('/extension-packages')
def extension_inventory():
    import extension_packages as packages
    try:return redact_structure({'items':packages.inventory()})
    except (OSError,ValueError,TypeError):raise HTTPException(409,'Package storage cannot be verified') from None


@router.post('/extension-packages/preview')
def extension_preview(body:ExtensionPackage):
    import extension_packages as packages
    try:
        payload,manifest,digest=packages.inspect(body.package_base64,body.expected_sha256)
        current=packages.read('package-'+manifest['id']+'.json')
        return redact_structure({'manifest':manifest,'sha256':digest,'size_bytes':len(payload),
                                 'current_sha256':current.get('sha256') if current else None,
                                 'trust':'unverified','enabled':False,'execution_supported':False})
    except (OSError,ValueError,TypeError,zipfile.BadZipFile):raise HTTPException(422,'Package manifest, bounds or digest invalid') from None


@router.post('/extension-packages/install')
def extension_install(body:ExtensionInstall):
    import extension_packages as packages
    if not body.confirmed:raise HTTPException(422,'Confirm disabled package installation')
    try:return redact_structure(packages.install(body.package_base64,body.expected_sha256,body.expected_current_sha256))
    except (OSError,ValueError,TypeError,zipfile.BadZipFile):raise HTTPException(409,'Package changed or storage failed; refresh') from None


@router.get('/extension-packages/{package_id}/versions')
def extension_versions(package_id:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100)):
    import extension_packages as packages
    if not packages.ID.fullmatch(package_id):raise HTTPException(422,'Invalid package ID')
    with f.connect() as db:
        records=rows(db,"SELECT id,payload_json,created_at FROM v5_events WHERE entity_id=? AND event_type='extension.package_stored' ORDER BY id DESC LIMIT ? OFFSET ?",(package_id,limit+1,offset))
    return {'items':[{'event_id':r['id'],'created_at':r['created_at'],**json.loads(r['payload_json'])} for r in records[:limit]],
            'offset':offset,'limit':limit,'has_more':len(records)>limit}


@router.post('/extension-packages/{package_id}/restore')
def extension_restore(package_id:str,body:ExtensionRestore):
    import extension_packages as packages
    if not body.confirmed:raise HTTPException(422,'Confirm stored version restoration')
    try:return redact_structure(packages.restore(package_id,body.sha256,body.expected_current_sha256))
    except (OSError,ValueError,TypeError,zipfile.BadZipFile):raise HTTPException(409,'Stored version changed or integrity failed') from None


class PackPreflight(BaseModel):
    model_config=ConfigDict(extra='forbid')
    manifest_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/capability-grants/{grant_id}/review')
def capability_review(grant_id: str):
    from v6_capabilities import capability_matches
    from v6_intents import LOCAL_SESSION_PRINCIPAL
    with f.connect() as db:
        grant=db.execute('SELECT * FROM capability_grants_v6 WHERE id=?',(grant_id,)).fetchone()
        if grant is None:raise HTTPException(404,'Grant not found')
        intent=db.execute('SELECT * FROM action_intents_v6 WHERE id=?',(grant['intent_id'],)).fetchone()
        try:eligible,reason=capability_matches(db,grant['intent_id'],grant_id,principal_id=grant['principal_id'])
        except (ValueError,TypeError,KeyError):eligible,reason=False,'invalid_record'
        used=db.execute('SELECT COUNT(*) FROM capability_uses_v6 WHERE grant_id=?',(grant_id,)).fetchone()[0]
        revoked=db.execute('SELECT reason_code FROM capability_revocations_v6 WHERE grant_id=?',(grant_id,)).fetchone()
        value={key:grant[key] for key in ('id','intent_id','task_id','capability','operation','resource_pattern','max_uses','approval_state','expires_at')}
        proposal={key:intent[key] for key in ('agent_id','principal_binding','resource','reason_summary','side_effect','scope_snapshot_id','policy_id')} if intent else None
        return redact_structure({'grant':value,'intent':proposal,'used_count':used,
                                 'remaining_uses':max(0,grant['max_uses']-used),'eligible':eligible,'reason_code':reason,
                                 'revoked_reason':revoked['reason_code'] if revoked else None,'executed':False,
                                 'approval_supported':True})


@router.post('/packs/{pack_id}/preflight')
def pack_preflight(pack_id: str, body: PackPreflight):
    import v6_packs
    try:return v6_packs.preflight(pack_id,body.manifest_sha256)
    except KeyError:raise HTTPException(404,'Pack not found') from None
    except ValueError:raise HTTPException(409,'Pack manifest changed; refresh') from None


class MonitorProcessStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmed_enabled_monitors_only: StrictBool


class MonitorProcessStop(BaseModel):
    model_config=ConfigDict(extra='forbid')
    service_id: str = Field(pattern=r'^[0-9a-f]{32}$')


@router.get('/monitor-process')
def managed_monitor_status():
    import monitor_process_control as control
    try:return control.status()
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Monitor process state cannot be verified') from None


@router.post('/monitor-process/start',status_code=202)
def start_monitor_process(body: MonitorProcessStart):
    import monitor_process_control as control
    if not body.confirmed_enabled_monitors_only:
        raise HTTPException(422,'Confirm enabled-monitor-only processing first')
    try:return control.start()
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Monitor process cannot start; check state and database compatibility') from None


@router.post('/monitor-process/stop',status_code=202)
def stop_monitor_process(body: MonitorProcessStop):
    import monitor_process_control as control
    try:return control.stop(body.service_id)
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Monitor process identity changed; refresh before stopping') from None


@router.post('/monitor-process/recover')
def recover_monitor_process(body: MonitorProcessStop):
    import monitor_process_control as control
    try:return control.recover(body.service_id)
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Launch recovery unavailable; refresh process state') from None


def rows(db, sql, params=()):
    return redact_structure([dict(row) for row in db.execute(sql, params)])


@router.get('/projects/{engagement_id}/summary')
def project_summary(engagement_id: str):
    with f.connect() as db:
        if not db.execute('SELECT 1 FROM engagements_v2 WHERE id=?', (engagement_id,)).fetchone():
            raise HTTPException(404, 'Project not found')
        tasks = rows(db, 'SELECT t.id,t.role,t.status,t.lease_owner,t.group_id FROM agent_tasks t JOIN research_campaigns c ON c.id=t.campaign_id WHERE c.engagement_id=? ORDER BY t.updated_at DESC LIMIT 50', (engagement_id,))
        count = db.execute('SELECT COUNT(*) FROM agent_tasks t JOIN research_campaigns c ON c.id=t.campaign_id WHERE c.engagement_id=?', (engagement_id,)).fetchone()[0]
        return {'tasks': tasks, 'task_count': count, 'has_more': count > len(tasks)}


@router.get('/agents/{tab}')
def agent_surface(tab: Literal['security','capabilities','memory','mcp','attack'], offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  agent_id: str | None = Query(None,max_length=200), q: str | None = Query(None,max_length=200)):
    # Each table has a different semantic source; no registry entry implies detection or trust.
    queries = {
        'security': 'SELECT id,name,provider,model,runtime,environment,status,risk,permissions_json,last_seen_at FROM agent_registry_v5 ORDER BY last_seen_at DESC,id',
        'capabilities': 'SELECT g.id,g.intent_id,g.task_id,g.capability,g.operation,g.resource_pattern,g.max_uses,(SELECT COUNT(*) FROM capability_uses_v6 u WHERE u.grant_id=g.id) used_count,g.approval_state,g.expires_at,r.reason_code revoked_reason,a.decision operator_decision,a.id approval_id,a.created_at reviewed_at FROM capability_grants_v6 g LEFT JOIN capability_revocations_v6 r ON r.grant_id=g.id LEFT JOIN capability_approvals_v6 a ON a.grant_id=g.id ORDER BY g.issued_at DESC,g.id',
        'memory': "SELECT id,agent_id,source_path,sha256,size_bytes,trust,created_at,CASE WHEN json_valid(metadata_json) THEN COALESCE(json_extract(metadata_json,'$.governance_state'),'active') ELSE 'unverified' END governance_state FROM agent_memory_snapshots_v5 ORDER BY created_at DESC,id",
        'mcp': 'SELECT id,agent_id,component_type,name,version,source,sha256,permissions_json,last_seen_at FROM agent_supply_components_v5 ORDER BY last_seen_at DESC,id',
        'attack': 'SELECT id,agent_id,scenario_id,mode,status,result_json,evidence_json,created_at FROM agent_attack_tests_v5 ORDER BY created_at DESC,id',
    }
    if tab!='memory' and (agent_id or q):
        raise HTTPException(422,'Memory filters apply only to memory snapshots')
    with f.connect() as db:
        if tab=='memory':
            query='SELECT * FROM ('+queries[tab]+') AS snapshot WHERE 1=1'
            args=[]
            if agent_id:
                query+=' AND agent_id=?';args.append(agent_id)
            if q and q.strip():
                query+=" AND instr(lower(id||' '||agent_id||' '||source_path||' '||sha256),lower(?))>0";args.append(q.strip())
            result=rows(db,query+' ORDER BY created_at DESC,id LIMIT ? OFFSET ?',(*args,limit+1,offset))
        else:
            result = rows(db, queries[tab] + ' LIMIT ? OFFSET ?', (limit+1, offset))
        extra = {}
        if tab == 'security':
            extra['decisions'] = rows(db, 'SELECT id,intent_id,decision,reasons_json,created_at FROM policy_decisions_v6 ORDER BY created_at DESC LIMIT 20')
        if tab == 'memory':
            extra['checkpoints'] = rows(db, 'SELECT id,task_id,lease_owner,created_at FROM agent_task_checkpoints ORDER BY created_at DESC LIMIT 20')
        if tab == 'attack':
            from v6_control import eval_runs
            extra['evaluations'] = eval_runs(limit=20)['runs']
        return {'items': result[:limit], 'offset': offset, 'has_more': len(result)>limit, **extra}


class AgentRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default='', max_length=200)
    model: str = Field(default='', max_length=200)
    environment: str = Field(default='local', max_length=200)


@router.post('/agents', status_code=201)
def register_agent(body: AgentRecord):
    agent_id, now = f.uid('agent'), f.utcnow()
    with f.connect() as db:
        db.execute('INSERT INTO agent_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (agent_id, body.name, body.provider, body.model, 'operator_registered', body.environment,
             'registered', 'unknown', '[]', '{}', 'operator_registration', now, now))
    return {'id': agent_id, 'status': 'registered', 'risk': 'unknown'}


class ComponentRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=200)
    component_type: Literal['mcp','adapter','library']
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=1000)


class ComponentRevision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    version: str = Field(min_length=1,max_length=100)
    source: str = Field(min_length=1,max_length=1000)
    reason: str = Field(min_length=3,max_length=2000)


def component_revision_token(row):
    return hashlib.sha256(json.dumps(dict(row),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def component_metadata(row):
    try:metadata=json.loads(row['metadata_json'])
    except (ValueError,TypeError):raise HTTPException(409,'Saved metadata cannot be verified') from None
    if not isinstance(metadata,dict):raise HTTPException(409,'Saved metadata cannot be verified')
    return metadata


@router.get('/components')
def component_inventory(offset: int = Query(0,ge=0),limit: int = Query(50,ge=1,le=100),
                        agent_id: str | None = Query(None,max_length=200),
                        component_type: Literal['mcp','adapter','library'] | None = None,
                        q: str | None = Query(None,max_length=200)):
    filters=[];args=[]
    if agent_id:filters.append('agent_id=?');args.append(agent_id)
    if component_type:filters.append('component_type=?');args.append(component_type)
    if q:
        filters.append("instr(lower(id||' '||name||' '||COALESCE(version,'')||' '||COALESCE(source,'')),lower(?))>0")
        args.append(q)
    sql='SELECT id,agent_id,component_type,name,version,source,sha256,last_seen_at FROM agent_supply_components_v5'
    if filters:sql+=' WHERE '+' AND '.join(filters)
    with f.connect() as db:
        items=rows(db,sql+' ORDER BY last_seen_at DESC,id LIMIT ? OFFSET ?',args+[limit+1,offset])
    return {'items':items[:limit],'offset':offset,'limit':limit,'has_more':len(items)>limit}


@router.get('/components/{component_id}')
def component_details(component_id: str):
    with f.connect() as db:
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE id=?',(component_id,)).fetchone()
        if row is None:raise HTTPException(404,'Component not found')
        metadata=component_metadata(row)
        from agent_attack_adapters import NAME as adapter_name,SOURCE as adapter_source,current as adapter_current,component_public_metadata
        if row['component_type']=='adapter' and row['name']==adapter_name and row['source']==adapter_source:
            metadata=component_public_metadata(adapter_current(db,row['agent_id']))
        value=dict(row)
        value['metadata_json']=metadata
        for field in ('permissions_json','capabilities_json'):
            try:value[field]=json.loads(value[field])
            except (ValueError,TypeError):raise HTTPException(409,'Component declarations cannot be verified') from None
        return {'component':redact_structure(value),'revision':component_revision_token(row),
                'history':redact_structure(metadata.get('operator_revisions',[])),
                'editable':metadata.get('source')=='operator_declared',
                'software_state':'not_checked','trust':'unverified'}


@router.post('/components/{component_id}/revisions')
def revise_component(component_id: str,body: ComponentRevision):
    if not body.version.strip() or not body.source.strip() or len(body.reason.strip())<3:
        raise HTTPException(422,'Version, source and revision reason are required')
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE id=?',(component_id,)).fetchone()
        if row is None:raise HTTPException(404,'Component not found')
        if component_revision_token(row)!=body.expected_revision:
            raise HTTPException(409,'Component changed; reload before revising')
        metadata=component_metadata(row)
        if metadata.get('source')!='operator_declared':
            raise HTTPException(409,'Detected component records cannot be rewritten as declarations')
        history=metadata.get('operator_revisions',[])
        if not isinstance(history,list) or len(history)>=100:
            raise HTTPException(409,'Component revision history limit reached')
        now=f.utcnow()
        history.append({'previous_version':row['version'],'previous_source':row['source'],'previous_sha256':row['sha256'],
                        'version':body.version,'source':body.source,'reason':body.reason,'created_at':now})
        metadata.update(operator_revisions=history,trust='unverified')
        try:
            db.execute('UPDATE agent_supply_components_v5 SET version=?,source=?,sha256=NULL,metadata_json=?,last_seen_at=? WHERE id=?',
                       (body.version,body.source,json.dumps(metadata),now,component_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Component source conflicts with another record') from None
    return {'id':component_id,'declaration_updated':True,'installed':False,'trust':'unverified'}


@router.post('/components', status_code=201)
def component_record(body: ComponentRecord):
    now, component_id = f.utcnow(), f.uid('component')
    with f.connect() as db:
        if not db.execute('SELECT 1 FROM agent_registry_v5 WHERE id=?', (body.agent_id,)).fetchone():
            raise HTTPException(404, 'Register the agent first')
        try:
            db.execute('INSERT INTO agent_supply_components_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (component_id, body.agent_id, body.component_type, body.name, body.version, body.source, None,
                 '[]', '{}', '{"source":"operator_declared","trust":"unverified"}', now, now))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Component already recorded') from None
    return {'id': component_id, 'trust': 'unverified', 'installed': False}


@router.get('/tasks/{task_id}/checkpoints')
def task_checkpoints(task_id: str, offset: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=100)):
    with f.connect() as db:
        if not db.execute('SELECT 1 FROM agent_tasks WHERE id=?', (task_id,)).fetchone():
            raise HTTPException(404, 'Task not found')
        items=[dict(row) for row in db.execute('SELECT * FROM agent_task_checkpoints WHERE task_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?',(task_id,limit+1,offset))]
        for item in items:
            item['checkpoint_json']=f.dump(redact_structure(f.load(item['checkpoint_json'],None)))
        return {'items':redact_structure(items[:limit]),'offset':offset,'limit':limit,'has_more':len(items)>limit}


@router.get('/tasks/{task_id}/context')
def task_record_context(task_id: str, checkpoint_offset: int = Query(0,ge=0), call_offset: int = Query(0,ge=0)):
    """Bounded investigation view, excluding prompts and tool grants."""
    with f.connect() as db:
        db.execute('BEGIN')
        task=db.execute('SELECT id,campaign_id,group_id,run_id,role,status,priority,attempt,max_attempts,lease_owner,lease_expires_at,budget_json,result_json,error_json,created_at,updated_at FROM agent_tasks WHERE id=?',(task_id,)).fetchone()
        if not task:raise HTTPException(404,'Task not found')
        value=dict(task)
        for key in ('budget','result','error'):
            value[key]=f.load(value.pop(key+'_json'),None)
        checkpoints=[dict(row) for row in db.execute('SELECT id,task_id,created_at,checkpoint_json FROM agent_task_checkpoints WHERE task_id=? ORDER BY created_at DESC,id LIMIT 101 OFFSET ?',(task_id,checkpoint_offset))]
        for item in checkpoints:item['checkpoint']=f.load(item.pop('checkpoint_json'),None)
        calls=[dict(row) for row in db.execute('SELECT id,state,attempt,reserved_tokens,reserved_cost_micros,created_at,updated_at FROM runtime_calls WHERE task_id=? ORDER BY created_at DESC,id LIMIT 101 OFFSET ?',(task_id,call_offset))]
        usage=db.execute('SELECT input_tokens,output_tokens,cost_micros,runtime_ms FROM agent_task_usage WHERE task_id=?',(task_id,)).fetchone()
    return redact_structure({'task':value,'usage':dict(usage) if usage else None,'checkpoints':checkpoints[:100],
                             'model_calls':calls[:100],'offsets':{'checkpoints':checkpoint_offset,'model_calls':call_offset},'has_more':{'checkpoints':len(checkpoints)>100,'model_calls':len(calls)>100},
                             'interpretation':'persisted_records_not_live_execution','excludes':['model_inputs','context_capsule','tool_grants']})


class PriorityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    priority: float = Field(ge=0, le=1000, allow_inf_nan=False)
    expected_updated_at: str


@router.patch('/tasks/{task_id}/priority')
def task_priority(task_id: str, body: PriorityInput):
    from v5_orchestration import _emit, get_task
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, 'Task not found')
        if task['status'] not in {'queued','paused'} or task['updated_at'] != body.expected_updated_at:
            raise HTTPException(409, 'Task changed or has already started; refresh first')
        db.execute('UPDATE agent_tasks SET priority=?,updated_at=? WHERE id=?', (body.priority, f.utcnow(), task_id))
        _emit(db, task['campaign_id'], task_id, 'task.priority_changed', {'priority': body.priority})
    return get_task(task_id)


class GroupLimits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_concurrency: int = Field(ge=1, le=32)
    expected_updated_at: str


@router.patch('/groups/{group_id}/concurrency')
def group_concurrency(group_id: str, body: GroupLimits):
    from v5_orchestration import _emit, get_group
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        group = db.execute('SELECT * FROM research_groups WHERE id=?', (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, 'Group not found')
        if group['updated_at'] != body.expected_updated_at or group['status'] == 'cancelled':
            raise HTTPException(409, 'Group changed; refresh first')
        active = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status IN ('leased','running')", (group_id,)).fetchone()[0]
        if active > body.max_concurrency:
            raise HTTPException(409, 'New limit is below active leases; pause the group first')
        budget = f.load(group['budget_json'], {})
        budget['max_concurrency'] = body.max_concurrency
        db.execute('UPDATE research_groups SET budget_json=?,updated_at=? WHERE id=?', (f.dump(budget),f.utcnow(),group_id))
        _emit(db, group['campaign_id'], group_id, 'group.concurrency_changed', {'max_concurrency': body.max_concurrency})
    return get_group(group_id)


def _event_query():
    return """
        SELECT 'run:'||e.id id,e.run_id,r.engagement_id,e.kind type,e.message summary,e.created_at,
               'run' source,NULL task_id,NULL artifact_id,r.synthetic synthetic
        FROM run_events_v2 e JOIN analysis_runs r ON r.id=e.run_id
        UNION ALL
        SELECT 'v5:'||v.id,t.run_id,COALESCE(c.engagement_id,tc.engagement_id),v.event_type,v.entity_id,v.created_at,
               CASE WHEN v.event_type LIKE 'call.%' OR v.event_type LIKE 'route.%' THEN 'model'
                    WHEN v.event_type LIKE 'tool.%' THEN 'tool' ELSE 'orchestrator' END,t.id,NULL,tr.synthetic
        FROM v5_events v
        LEFT JOIN runtime_calls mc ON mc.id=v.entity_id AND v.event_type LIKE 'call.%'
        LEFT JOIN runtime_route_decisions rd ON rd.id=v.entity_id AND v.event_type LIKE 'route.%'
        LEFT JOIN http_gateway_executions_v6 hx ON hx.action_id=v.entity_id AND v.event_type LIKE 'tool.execution.%'
        LEFT JOIN policy_decisions_v6 pd ON pd.id=v.entity_id AND v.event_type='policy.decided'
        LEFT JOIN action_intents_v6 ai ON ai.id=pd.intent_id
        LEFT JOIN agent_tasks t ON t.id=CASE WHEN v.event_type LIKE 'call.%' THEN mc.task_id
             WHEN v.event_type LIKE 'route.%' THEN rd.task_id
             WHEN v.event_type LIKE 'tool.execution.%' THEN hx.task_id
             WHEN v.event_type='policy.decided' THEN ai.task_id ELSE v.entity_id END
             AND (v.campaign_id IS NULL OR t.campaign_id=v.campaign_id)
             AND (mc.id IS NULL OR (mc.campaign_id=t.campaign_id AND mc.group_id IS t.group_id))
             AND (rd.id IS NULL OR rd.campaign_id=t.campaign_id)
        LEFT JOIN research_campaigns c ON c.id=v.campaign_id
        LEFT JOIN research_campaigns tc ON tc.id=t.campaign_id
        LEFT JOIN analysis_runs tr ON tr.id=t.run_id
        UNION ALL
        SELECT 'monitor:'||e.id,a.run_id,a.id,'agent.telemetry',e.observation_id,ar.created_at,
               'monitor',NULL,e.artifact_id,a.demo
        FROM agent_events e JOIN agent_audits a ON a.id=e.audit_id
        JOIN artifacts ar ON ar.id=e.artifact_id AND ar.run_id=a.run_id
    """


@router.get('/records/{kind}')
def workspace_records(kind: Literal['events','evidence','incidents'], engagement_id: str | None = None,
                      offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                      source: Literal['run','orchestrator','model','tool','monitor'] | None = None,
                      task_id: str | None = Query(None,max_length=200),
                      q: str | None = Query(None,max_length=200),
                      artifact_kind: str | None = Query(None,max_length=100)):
    if artifact_kind and kind != 'evidence':
        raise HTTPException(422, 'Artifact type applies only to evidence')
    if kind != 'events' and (source or task_id):
        raise HTTPException(422, 'Source and task filters apply only to events')
    with f.connect() as db:
        if kind == 'events':
            sql = _event_query()
        elif kind == 'evidence':
            sql = 'SELECT a.id,a.run_id,r.engagement_id,a.kind type,a.sha256,a.media_type,a.created_at FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id'
        else:
            sql = 'SELECT c.id,c.run_id,r.engagement_id,c.title,c.category,c.status,c.created_at FROM candidate_findings c JOIN analysis_runs r ON r.id=c.run_id'
        query, args, filters = 'SELECT * FROM ('+sql+') AS record', [], []
        for name,value in [('engagement_id',engagement_id),('source',source),('task_id',task_id)]:
            if value:
                filters.append(name+'=?')
                args.append(value)
        if artifact_kind:
            filters.append('type=?')
            args.append(artifact_kind)
        if q and q.strip():
            fields = ['id','run_id','type','summary'] if kind=='events' else ['id','run_id','type','sha256'] if kind=='evidence' else ['id','run_id','title','category','status']
            filters.append("instr(lower("+"||' '||".join("COALESCE("+name+",'')" for name in fields)+"),lower(?))>0")
            args.append(q.strip())
            if kind=='evidence':
                filters[-1]='('+filters[-1]+" OR EXISTS(SELECT 1 FROM evidence_v2 e LEFT JOIN observations o ON o.id=e.observation_id AND o.run_id=e.run_id WHERE e.run_id=record.run_id AND (e.artifact_id=record.id OR o.raw_ref=record.id) AND instr(lower(e.summary||' '||COALESCE(o.subject,'')||' '||COALESCE(o.summary,'')),lower(?))>0))"
                args.append(q.strip())
        if filters:
            query += ' WHERE '+' AND '.join(filters)
        result = rows(db, query+' ORDER BY created_at DESC,id LIMIT ? OFFSET ?', (*args,limit+1,offset))
        return {'items': result[:limit], 'has_more': len(result)>limit, 'offset': offset}


@router.get('/campaigns/{campaign_id}/paths')
def graph_paths(campaign_id: str, source_id: str, target_id: str, max_depth: int = Query(6, ge=1, le=10)):
    with f.connect() as db:
        nodes = {r['id']:dict(r) for r in db.execute('SELECT id,title,node_type FROM research_nodes WHERE campaign_id=?', (campaign_id,))}
        if source_id not in nodes or target_id not in nodes:
            raise HTTPException(404, 'Both nodes must belong to the selected campaign')
        edges = rows(db,'SELECT id,source_id,target_id,relation_type FROM research_edges WHERE campaign_id=? ORDER BY id LIMIT 5001',(campaign_id,))
        if len(edges)>5000:
            raise HTTPException(409, 'Graph exceeds interactive path limit')
    adjacency = {}
    for e in edges: adjacency.setdefault(e['source_id'],[]).append(e)
    found, stack, steps = [], [(source_id,[],{source_id})], 0
    while stack and len(found)<20 and steps<10000:
        current, path, seen = stack.pop(); steps += 1
        if current == target_id:
            found.append(path); continue
        if len(path)>=max_depth: continue
        for e in adjacency.get(current,[]):
            if e['target_id'] not in seen: stack.append((e['target_id'],path+[e],seen|{e['target_id']}))
    return {'paths':found, 'nodes':list(nodes.values()), 'truncated':bool(stack), 'max_depth':max_depth,
            'interpretation':'recorded_directed_relationships_not_verified_exploits'}


def backup_root():
    return f.LOCAL_DATA_ROOT / 'backups'


class WorkspaceExport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engagement_id: str | None = Field(default=None,min_length=1,max_length=200)
    redaction: Literal['standard','strict'] = 'strict'


@router.post('/exports')
def export_workspace(body: WorkspaceExport):
    """Export a consistent, allowlisted record index without reading evidence files."""
    from report_options import strict_redact
    from version import APP_VERSION, SCHEMA_VERSION
    queries={
        'research':'SELECT r.id,r.engagement_id,r.mode,r.status,r.synthetic,r.created_at FROM analysis_runs r',
        'tasks':'SELECT t.id,t.run_id,t.role,t.status,t.created_at FROM agent_tasks t JOIN analysis_runs r ON r.id=t.run_id',
        'candidates':'SELECT c.id,c.run_id,c.title,c.category,c.status,c.created_at FROM candidate_findings c JOIN analysis_runs r ON r.id=c.run_id',
        'findings':'SELECT c.id,c.candidate_id,c.engagement_id,c.title,c.severity,c.status,c.created_at FROM canonical_findings c JOIN candidate_findings candidate ON candidate.id=c.candidate_id JOIN analysis_runs r ON r.id=candidate.run_id',
        'artifacts':'SELECT a.id,a.run_id,a.kind,a.sha256,a.media_type,a.redacted,a.created_at FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id',
    }
    with f.connect() as db:
        db.execute('BEGIN')
        if body.engagement_id and not db.execute('SELECT 1 FROM engagements_v2 WHERE id=?',(body.engagement_id,)).fetchone():
            raise HTTPException(404,'Project not found')
        records={}
        for kind,query in queries.items():
            args=()
            if body.engagement_id:
                query+=' WHERE r.engagement_id=?';args=(body.engagement_id,)
            result=rows(db,query+' ORDER BY 1 LIMIT 10001',args)
            if len(result)>10000:
                raise HTTPException(409,'Export exceeds 10000 records per category; select a smaller project')
            records[kind]=result
    value={'format':'fieldwork-record-index-v1','created_at':f.utcnow(),'app_version':APP_VERSION,
           'schema':SCHEMA_VERSION,'engagement_id':body.engagement_id,'redaction':body.redaction,
           'counts':{kind:len(items) for kind,items in records.items()},'records':records,
           'excludes':['artifact_bytes','local_paths','credentials','model_inputs','authorization_grants'],
           'restorable':False,'proof_status':'index_only_not_a_verification_receipt'}
    if body.redaction=='strict':value=strict_redact(value)
    payload=json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8')
    if len(payload)>16*1024*1024:
        raise HTTPException(409,'Export exceeds 16 MiB; select a smaller project')
    from fastapi.responses import Response
    return Response(payload,media_type='application/json',headers={
        'Content-Disposition':'attachment; filename="fieldwork-record-index.json"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/dependency-inventory')
def dependency_inventory():
    from dependency_inventory import inventory
    try:return inventory()
    except (ValueError,OSError) as error:raise HTTPException(409,'Dependency metadata cannot be verified') from error


@router.get('/dependency-inventory/sbom')
def dependency_sbom(expected_revision:str|None=Query(default=None,pattern=r'^[0-9a-f]{64}$')):
    from dependency_inventory import sbom
    from fastapi.responses import Response
    value=dependency_inventory()
    if expected_revision and expected_revision!=value['revision']:raise HTTPException(409,'Dependency inventory changed; reload before export')
    payload=json.dumps(sbom(value),ensure_ascii=False,indent=2)
    return Response(payload,media_type='application/vnd.cyclonedx+json',headers={'Content-Disposition':'attachment; filename="fieldwork-python-bom.json"','Cache-Control':'no-store'})


@router.get('/maintenance')
def maintenance():
    from lifecycle import list_backups
    from version import APP_VERSION, BUILD_NUMBER, SCHEMA_VERSION
    from evidence_backups import list_archives
    backups = list_backups(backup_root())
    return {'app_version':APP_VERSION,'build':BUILD_NUMBER,'schema':SCHEMA_VERSION,
            'data_directory':str(f.LOCAL_DATA_ROOT), 'database_bytes':f.DB.stat().st_size if f.DB.exists() else 0,
            'retention':'manual_no_automatic_deletion','background':'requires_app_backend',
            'updates':'local_build_no_release_feed',
            'backups':[{k:v for k,v in b.items() if k not in {'database_path','manifest_path'}} for b in backups],
            'evidence_archives':list_archives(backup_root())}


class MigrationPreflight(BaseModel):
    model_config=ConfigDict(extra='forbid')
    destination: str = Field(min_length=1,max_length=4096)


@router.post('/storage-migration/preflight')
def storage_migration_preflight(body:MigrationPreflight):
    from storage_migration import preflight
    try:
        return preflight(f.LOCAL_DATA_ROOT,body.destination)
    except (ValueError,OSError) as error:
        message=str(error) if isinstance(error,ValueError) else 'directory_unavailable'
        raise HTTPException(409,message) from error


@router.post('/backups',status_code=201)
def create_backup():
    from lifecycle import backup_database
    result = backup_database(f.DB,backup_root(),f.ROOT,'operator-'+uuid.uuid4().hex[:8])
    if not result: raise HTTPException(409,'Database unavailable')
    return {k:v for k,v in result.items() if k not in {'database_path','manifest_path'}}


@router.post('/backups/{backup_id}/restore-drill')
def restore_drill(backup_id: str):
    from lifecycle import list_backups, database_integrity
    if Path(backup_id).name != backup_id or '\\' in backup_id:
        raise HTTPException(422,'Invalid backup identifier')
    record = next((b for b in list_backups(backup_root()) if b['id']==backup_id),None)
    if not record or not record['valid']: raise HTTPException(409,'Backup missing or integrity check failed')
    root = f.LOCAL_DATA_ROOT/'restore-drills'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest=root/(uuid.uuid4().hex+'.db')
    with sqlite3.connect(f'file:{record["database_path"]}?mode=ro',uri=True) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    dest.chmod(0o600)
    valid=database_integrity(dest)
    result={'backup_id':backup_id,'integrity_ok':valid,'restored_to_isolated_copy':True,'live_database_replaced':False}
    dest.unlink()
    return result


class TeamSize(BaseModel):
    model_config = ConfigDict(extra='forbid')
    researcher: int = Field(ge=0, le=100, strict=True)
    explorer: int = Field(ge=0, le=100, strict=True)
    specialist: int = Field(ge=0, le=100, strict=True)
    expected_updated_at: str


def _resize_members(db, group_id):
    tasks = db.execute('SELECT * FROM agent_tasks WHERE group_id=? ORDER BY created_at,id', (group_id,)).fetchall()
    recorded = {r[0] for r in db.execute('SELECT t.id FROM agent_tasks t WHERE t.group_id=? AND '
                '(EXISTS(SELECT 1 FROM runtime_calls c WHERE c.task_id=t.id) OR '
                'EXISTS(SELECT 1 FROM agent_task_usage u WHERE u.task_id=t.id))', (group_id,))}
    protected = {t['id'] for t in tasks if t['attempt'] or t['id'] in recorded
                 or t['status'] not in ('queued','paused','cancelled') or t['lease_owner']}
    return tasks, protected


@router.get('/groups/{group_id}/size')
def team_size(group_id: str):
    with f.connect() as db:
        group = db.execute('SELECT * FROM research_groups WHERE id=?', (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, 'Group not found')
        tasks, protected = _resize_members(db, group_id)
        counts = {role:sum(t['role']==role and t['status']!='cancelled' for t in tasks)
                  for role in ('researcher','explorer','specialist')}
        minimum = {role:sum(t['role']==role and t['status']!='cancelled' and t['id'] in protected for t in tasks)
                   for role in counts}
        return {'roles': counts, 'minimum_roles': minimum, 'updated_at': group['updated_at'],
                'editable': bool(tasks) and group['strategy'] == 'bounded_research' and group['status'] in ('active','paused')
                  and all(f.load(t['context_capsule_json'], {}).get('team_plan') for t in tasks)}


@router.patch('/groups/{group_id}/size')
def resize_team(group_id: str, body: TeamSize):
    from v5_orchestration import _emit, _continuous_scope_current, get_group
    desired = {role: getattr(body, role) for role in ('researcher','explorer','specialist')}
    total = sum(desired.values())
    if not 1 <= total <= 100:
        raise HTTPException(422, 'Team size must be between 1 and 100')
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        group = db.execute('SELECT * FROM research_groups WHERE id=?', (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, 'Group not found')
        if group['updated_at'] != body.expected_updated_at or group['status'] not in ('active','paused') or group['strategy'] != 'bounded_research':
            raise HTTPException(409, 'Group changed or cannot be resized; refresh first')
        tasks, protected = _resize_members(db, group_id)
        if not tasks:
            raise HTTPException(409, 'Team has no task template')
        if any(not f.load(t['context_capsule_json'], {}).get('team_plan') for t in tasks):
            raise HTTPException(409, 'Team contains independently managed tasks')
        if not all(_continuous_scope_current(db, t) for t in tasks):
            raise HTTPException(409, 'Team authorization is stale')
        budget = f.load(group['budget_json'], {})
        cost = budget.get('max_cost_micros', 0)
        fixed = [t for t in tasks if t['id'] in protected]
        minimum = {role:sum(t['role']==role and t['status']!='cancelled' for t in fixed) for role in desired}
        if any(desired[role] < minimum[role] for role in desired):
            raise HTTPException(409, 'Requested size would remove tasks with execution history; preserve protected members')
        fixed_cost = 0
        for task in fixed:
            cap = f.load(task['budget_json'], {}).get('max_cost_micros')
            if type(cap) is not int or cap < 0:
                raise HTTPException(409, 'Protected task budget is not bounded')
            fixed_cost += cap
        available = cost - fixed_cost
        mutable_count = total - sum(minimum.values())
        if available < mutable_count:
            raise HTTPException(409, 'Remaining budget cannot cover requested size while preserving protected task allowances')
        now = f.utcnow()
        kept = []
        added = []
        removed = []
        for role, count in desired.items():
            members = [t for t in tasks if t['role'] == role and t['status'] != 'cancelled' and t['id'] not in protected]
            count -= minimum[role]
            kept.extend(t['id'] for t in members[:count])
            for task in members[count:]:
                db.execute("UPDATE agent_tasks SET status='cancelled',updated_at=? WHERE id=?", (now, task['id']))
                removed.append(task['id'])
                _emit(db, group['campaign_id'], task['id'], 'task.cancelled', {'reason': 'team_resize'})
            template = next((t for t in tasks if t['role'] == role), tasks[0])
            for _ in range(max(0, count-len(members))):
                task_id = 'atask-' + uuid.uuid4().hex
                capsule = f.load(template['context_capsule_json'], {})
                capsule['resized_from_task_id'] = template['id']
                capsule['role_index'] = 1 + sum(t['role'] == role for t in tasks) + len(added)
                db.execute('INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (task_id, group['campaign_id'], group['run_id'], group_id, role,
                            group['objective']+' · '+role+' '+str(capsule['role_index']),
                            f.dump(capsule), template['tool_grants_json'], template['route_requirement_json'],
                            template['budget_json'], 0, 'paused' if group['status']=='paused' else 'queued',
                            0, template['max_attempts'], 'resize:'+task_id, None,None,None,None,None,now,now))
                kept.append(task_id)
                added.append(task_id)
                _emit(db, group['campaign_id'], task_id, 'task.created', {'reason':'team_resize'})
        for index, task_id in enumerate(kept):
            task = db.execute('SELECT budget_json FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
            allowance = f.load(task['budget_json'], {})
            allowance['max_cost_micros'] = available//mutable_count + (1 if index < available%mutable_count else 0)
            db.execute('UPDATE agent_tasks SET budget_json=?,updated_at=? WHERE id=?', (f.dump(allowance),now,task_id))
        budget['max_tasks'] = len(tasks)+len(added)
        budget['max_concurrency'] = min(budget.get('max_concurrency',1), total)
        db.execute('UPDATE research_groups SET budget_json=?,updated_at=? WHERE id=?', (f.dump(budget),now,group_id))
        _emit(db, group['campaign_id'], group_id, 'team.resized', {'roles':desired,'added':added,'removed':removed,'cost_budget_unchanged':True,'protected_tasks':sorted(protected),'preserved_allowance_micros':fixed_cost})
    return {'group':get_group(group_id),'size':team_size(group_id),'added':added,'removed':removed}


class MemoryImport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent_id: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=65536)
    retention: Literal['manual'] = 'manual'


@router.post('/memory-snapshots', status_code=201)
def import_memory_snapshot(body: MemoryImport):
    from reporting import redact
    content = body.content
    if '\x00' in content or len(content.encode('utf-8')) > 262144:
        raise HTTPException(422, 'Memory must be bounded UTF-8 text without NUL bytes')
    try:
        value = json.loads(content)
    except (ValueError, RecursionError):
        cleaned = redact(content)
    else:
        try:
            cleaned = json.dumps(redact_structure(value), ensure_ascii=False, indent=2)
        except RecursionError:
            raise HTTPException(422, 'Memory JSON nesting is too deep')
    digest = hashlib.sha256(cleaned.encode('utf-8')).hexdigest()
    source = redact(body.source_name.strip())
    if not source or any(ord(c)<32 for c in source):
        raise HTTPException(422, 'Source name must be a printable label')
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM agent_registry_v5 WHERE id=?',(body.agent_id,)).fetchone():
            raise HTTPException(404, 'Registered agent not found')
        matches = db.execute('SELECT id,metadata_json FROM agent_memory_snapshots_v5 WHERE agent_id=? AND source_path=? AND sha256=?',
                             (body.agent_id,source,digest)).fetchall()
        for existing in matches:
            if f.load(existing['metadata_json'], {}).get('format')=='operator_text_v1':
                _memory_value(db, existing['id'])
                return {'id':existing['id'],'sha256':digest,'deduplicated':True,'trust':'untrusted','execution':'none'}
        snapshot_id, now = f.uid('memory'), f.utcnow()
        metadata = {'format':'operator_text_v1','content':cleaned,'retention':body.retention,
                    'redaction':'common_secret_patterns','source_is_label':True,'automatic_model_context':False}
        db.execute('INSERT INTO agent_memory_snapshots_v5 VALUES(?,?,?,?,?,?,?,?,?)',
                   (snapshot_id,body.agent_id,source,digest,len(cleaned.encode('utf-8')),None,'untrusted',f.dump(metadata),now))
    return {'id':snapshot_id,'sha256':digest,'deduplicated':False,'trust':'untrusted','execution':'none'}


def _memory_value(db, snapshot_id):
    row = db.execute('SELECT * FROM agent_memory_snapshots_v5 WHERE id=?',(snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Memory snapshot not found')
    metadata = component_metadata(row)
    content = metadata.get('content') if metadata.get('format')=='operator_text_v1' else None
    if content is not None and (not isinstance(content,str) or hashlib.sha256(content.encode('utf-8')).hexdigest()!=row['sha256']):
        raise HTTPException(409, 'Memory snapshot integrity check failed')
    return {'id':row['id'],'agent_id':row['agent_id'],'source_name':row['source_path'],'sha256':row['sha256'],
            'trust':row['trust'],'created_at':row['created_at'],'content':content,'retention':metadata.get('retention','unspecified'),
            'automatic_model_context':False,'governance_state':metadata.get('governance_state','active'),
            'governance_history':redact_structure(metadata.get('governance_history',[])),
            'revision':component_revision_token(row)}


class MemoryGovernance(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: Literal['active','retired']
    reason: str = Field(min_length=3,max_length=1000)


@router.post('/memory-snapshots/{snapshot_id}/governance')
def memory_governance(snapshot_id:str,body:MemoryGovernance):
    if len(body.reason.strip())<3:raise HTTPException(422,'Governance reason required')
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        value=_memory_value(db,snapshot_id)
        if value['revision']!=body.expected_revision:raise HTTPException(409,'Snapshot changed; reload')
        row=db.execute('SELECT * FROM agent_memory_snapshots_v5 WHERE id=?',(snapshot_id,)).fetchone()
        metadata=component_metadata(row)
        history=metadata.get('governance_history',[])
        if not isinstance(history,list) or len(history)>=100:raise HTTPException(409,'Governance history limit reached')
        if value['governance_state']==body.state:raise HTTPException(409,'Snapshot already in this state')
        history.append({'previous_state':value['governance_state'],'state':body.state,'reason':body.reason,'created_at':f.utcnow()})
        metadata.update(governance_state=body.state,governance_history=history)
        db.execute('UPDATE agent_memory_snapshots_v5 SET metadata_json=? WHERE id=?',(json.dumps(metadata),snapshot_id))
    return {'id':snapshot_id,'governance_state':body.state,'content_deleted':False,'automatic_model_context':False}


@router.get('/memory-snapshots/{snapshot_id}')
def memory_snapshot(snapshot_id: str, compare_to: str | None = None):
    import difflib
    with f.connect() as db:
        value = _memory_value(db, snapshot_id)
        if compare_to:
            previous = _memory_value(db, compare_to)
            if (previous['agent_id'],previous['source_name']) != (value['agent_id'],value['source_name']):
                raise HTTPException(409, 'Compare snapshots of the same agent and source')
            if previous['content'] is None or value['content'] is None:
                raise HTTPException(409, 'Snapshot has metadata only; text comparison is unavailable')
            # Bound the display independently of input size; the snapshots stay intact.
            if max(len(previous['content'].splitlines()),len(value['content'].splitlines()))>2000:
                raise HTTPException(422, 'Text comparison supports at most 2000 lines per snapshot')
            lines = list(difflib.unified_diff(previous['content'].splitlines(),value['content'].splitlines(),fromfile=compare_to,tofile=snapshot_id))
            value['diff'] = '\n'.join(lines[:500])
            value['diff_truncated'] = len(lines)>500
    return redact_structure(value)


@router.get('/artifacts/{artifact_id}/relations')
def artifact_relations(artifact_id: str):
    with f.connect() as db:
        db.execute('BEGIN')
        artifact = db.execute('SELECT a.id,a.run_id,a.kind,a.sha256,a.media_type,a.redacted,a.created_at,r.engagement_id,r.synthetic '
                              'FROM artifacts a JOIN analysis_runs r ON r.id=a.run_id WHERE a.id=?',(artifact_id,)).fetchone()
        if not artifact:
            raise HTTPException(404,'Artifact not found')
        run_id=artifact['run_id']
        queries={
            'observations': ('SELECT id,observation_type,subject,summary,confidence,source_capability,created_at FROM observations '
                             'WHERE run_id=? AND raw_ref=? ORDER BY created_at,id',(run_id,artifact_id)),
            'evidence': ('SELECT e.id,e.observation_id,e.evidence_type,e.summary,e.polarity,e.created_at FROM evidence_v2 e '
                         'LEFT JOIN observations o ON o.id=e.observation_id AND o.run_id=e.run_id '
                         'WHERE e.run_id=? AND (e.artifact_id=? OR o.raw_ref=?) ORDER BY e.created_at,e.id',(run_id,artifact_id,artifact_id)),
            'tasks': ('SELECT DISTINCT t.id,t.role,t.status,x.action_id,x.runner_id FROM runtime_artifact_links_v6 l '
                      'JOIN http_gateway_executions_v6 x ON x.action_id=l.action_id JOIN agent_tasks t ON t.id=x.task_id '
                      'WHERE l.artifact_id=? AND t.run_id=? ORDER BY t.id,x.action_id',(artifact_id,run_id)),
        }
        # Candidate evidence references are matched by exact IDs, never by substring.
        candidates_sql="""SELECT DISTINCT c.id,c.title,c.category,c.status,c.run_id FROM candidate_findings c
            WHERE c.run_id=? AND EXISTS (
              SELECT 1 FROM json_each(CASE WHEN json_valid(c.evidence_ids) THEN c.evidence_ids ELSE '[]' END) ref
              JOIN evidence_v2 e ON e.id=ref.value AND e.run_id=c.run_id
              LEFT JOIN observations o ON o.id=e.observation_id AND o.run_id=e.run_id
              WHERE e.artifact_id=? OR o.raw_ref=?) ORDER BY c.created_at,c.id"""
        queries['candidates']=(candidates_sql,(run_id,artifact_id,artifact_id))
        result={'artifact':dict(artifact),'integrity':'stored_digest_only','has_more':{}}
        for key,(sql,args) in queries.items():
            values=rows(db,sql+' LIMIT 101',args)
            result[key]=values[:100]
            result['has_more'][key]=len(values)>100
        candidate_ids=[v['id'] for v in result['candidates']]
        result['verifications']=[];result['findings']=[]
        if candidate_ids:
            placeholders=','.join('?' for _ in candidate_ids)
            for key,sql in [
                ('verifications','SELECT id,candidate_id,oracle,status,started_at,completed_at FROM verification_attempts WHERE candidate_id IN ('+placeholders+') ORDER BY started_at,id'),
                ('findings','SELECT DISTINCT f.id,f.title,f.severity,f.status FROM canonical_findings f LEFT JOIN finding_occurrences o ON o.finding_id=f.id WHERE f.candidate_id IN ('+placeholders+') OR o.candidate_id IN ('+placeholders+') ORDER BY f.id')]:
                args=candidate_ids if key=='verifications' else candidate_ids+candidate_ids
                values=rows(db,sql+' LIMIT 101',args)
                result[key]=values[:100];result['has_more'][key]=len(values)>100
        result['derived_relations_partial']=result['has_more']['candidates']
        result['association_scope']='same_run_recorded_references'
        result['file_content_read']=False
    return redact_structure(result)


class PolicyCheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engagement_id: str = Field(min_length=1, max_length=128)


@router.post('/policy-checks', status_code=201)
def run_policy_check(body: PolicyCheckInput):
    """Execute fixed local policy cases in a dedicated synthetic Run, never against an agent."""
    from v6_eval_seeds import run_policy_seed
    run_id = f.uid('eval-run')
    artifact_root = f.LOCAL_DATA_ROOT / 'evals' / run_id
    try:
        with f.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            project = db.execute('SELECT * FROM engagements_v2 WHERE id=?', (body.engagement_id,)).fetchone()
            if not project:
                raise HTTPException(404, 'Project not found')
            db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, project['id'], project['mode'], project['current_scope_snapshot_id'],
                        project['current_policy_id'], 'completed', 'policy_eval', 1,
                        None, None, None, None, f.utcnow()))
            eval_id = run_policy_seed(db, run_id, artifact_root)
            row = db.execute('SELECT * FROM eval_runs_v6 WHERE id=?', (eval_id,)).fetchone()
            result = {'id':eval_id, 'run_id':run_id, 'artifact_id':row['artifact_id'],
                      'metrics':f.load(row['metrics_json'],{}), 'result':f.load(row['result_json'],{}),
                      'subject':'local_policy_engine', 'synthetic':True,
                      'network_requests':0, 'model_calls':0, 'agent_tested':False}
    except Exception:
        # Only this newly generated evaluation directory can be removed on rollback.
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
        raise
    return result


@router.get('/campaigns/{campaign_id}/path-nodes')
def path_nodes(campaign_id: str, q: str = Query('', max_length=200),
               offset: int = Query(0, ge=0, le=1000000), limit: int = Query(50, ge=1, le=100)):
    with f.connect() as db:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM research_campaigns WHERE id=?',(campaign_id,)).fetchone():
            raise HTTPException(404,'Research campaign not found')
        sql='SELECT id,title,node_type,status FROM research_nodes WHERE campaign_id=?'
        args=[campaign_id]
        if q.strip():
            sql+=" AND instr(lower(id||' '||title||' '||node_type),lower(?))>0"
            args.append(q.strip())
        result=rows(db,sql+' ORDER BY created_at,id LIMIT ? OFFSET ?',(*args,limit+1,offset))
    return {'items':result[:limit],'has_more':len(result)>limit,'offset':offset}


@router.post('/evidence-backups',status_code=201)
def create_evidence_backup():
    from evidence_backups import create_archive
    try:
        return create_archive(f.DB,f.LOCAL_DATA_ROOT,backup_root())
    except ValueError as error:
        raise HTTPException(409,str(error)) from None
    except (OSError,sqlite3.Error):
        raise HTTPException(409,'Evidence backup could not be created') from None


@router.post('/evidence-backups/{backup_id}/restore-drill')
def drill_evidence_backup(backup_id: str):
    import zipfile
    from evidence_backups import restore_drill
    try:
        return restore_drill(backup_root(),backup_id)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,zipfile.BadZipFile):
        raise HTTPException(409,'Evidence backup is missing, invalid, or incomplete') from None


@router.get('/evidence-backups/{backup_id}/restore-plan')
def evidence_restore_plan(backup_id: str):
    import zipfile
    from evidence_backups import restore_plan
    try:
        return restore_plan(f.DB,backup_root(),backup_id)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,zipfile.BadZipFile):
        raise HTTPException(409,'Restore prerequisites could not be verified') from None


class MonitorStartupInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: StrictBool
    confirmed_enabled_monitors_only: StrictBool
    preview_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/monitor-startup')
def monitor_startup_plan():
    import monitor_startup
    try:return monitor_startup.plan()
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Login startup state cannot be verified') from None


@router.post('/monitor-startup')
def configure_monitor_startup(body: MonitorStartupInput):
    if not body.confirmed_enabled_monitors_only:raise HTTPException(422,'Confirm enabled-monitor-only login processing first')
    import monitor_startup
    try:return monitor_startup.apply(body.enabled,body.preview_hash)
    except (OSError,ValueError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Login startup change failed; refresh and review process, configuration and database state') from None


class MonitorAlertConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool
    expected_revision: int | None = Field(default=None, ge=1)
    confirmed_local_only: StrictBool


class MonitorAlertAcknowledgement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


@router.get('/monitor-alerts/configuration')
def monitor_alert_configuration():
    import monitor_alerts
    return monitor_alerts.configuration()


@router.post('/monitor-alerts/configuration')
def configure_monitor_alerts(body: MonitorAlertConfiguration):
    import monitor_alerts
    if not body.confirmed_local_only:
        raise HTTPException(422, 'Confirm local inbox delivery only')
    return monitor_alerts.configure(body.enabled, body.expected_revision)


@router.get('/monitor-alerts')
def monitor_alert_inbox(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
    import monitor_alerts
    return monitor_alerts.inbox(offset, limit)


@router.post('/monitor-alerts/{window_id}/acknowledge')
def acknowledge_monitor_alert(window_id: str, body: MonitorAlertAcknowledgement):
    import monitor_alerts
    return monitor_alerts.acknowledge(window_id, body.review_hash)


@router.get('/monitor-alerts/{window_id}/source')
def monitor_alert_source(window_id: str):
    import monitor_alerts
    return monitor_alerts.source(window_id)


class MonitorWindowReplay(MonitorAlertAcknowledgement):
    confirmed_policy_only: StrictBool


@router.get('/monitor-alerts/{window_id}/reconstruction')
def monitor_window_reconstruction_receipt(window_id: str):
    import monitor_window_replay
    try:return monitor_window_replay.receipt(window_id)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Reconstruction receipt cannot be verified') from None


@router.post('/monitor-alerts/{window_id}/reconstruction')
def reconstruct_monitor_window(window_id: str, body: MonitorWindowReplay):
    import monitor_window_replay
    if not body.confirmed_policy_only:
        raise HTTPException(422, 'Confirm policy reconstruction only; no behavior verification')
    try:return monitor_window_replay.replay(window_id, body.review_hash)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,sqlite3.Error):raise HTTPException(409,'Reconstruction storage or implementation unavailable; refresh') from None


@router.get('/monitor-alert-native-delivery')
def monitor_native_delivery(after: int | None = Query(None, ge=0, le=9223372036854775807)):
    import monitor_alerts
    try:return monitor_alerts.native_delivery(after)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):
        raise HTTPException(409,'Native notification source cannot be verified') from None

# Keep attack-request persistence separate from executable attack adapters.
from agent_attack_requests import router as attack_request_router
router.include_router(attack_request_router)
from agent_attack_adapters import router as attack_adapter_router
router.include_router(attack_adapter_router)
from agent_attack_dispatch import router as attack_dispatch_router
router.include_router(attack_dispatch_router)
