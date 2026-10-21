"""Operator-reviewed local adapter versions; registration never executes code."""
import ast
import hashlib
import json
from fastapi import APIRouter,HTTPException,Path
from pydantic import BaseModel,ConfigDict,Field,StrictBool
import final_core as core
import agent_adapter_history as history
from campaign_materials import access
from agent_attack_executor import PROTOCOL,TOOL_PROTOCOL,MAX_SOURCE_BYTES

router=APIRouter(prefix='/agent-attack-requests/adapters',tags=['Agent attack adapters'])
NAME='Fieldwork local attack adapter'
SOURCE='operator_reviewed_local_python'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def current(db,agent_id):
    if not db.execute('SELECT 1 FROM agent_registry_v5 WHERE id=?',(agent_id,)).fetchone():
        raise HTTPException(404,'Registered agent not found')
    row=db.execute("SELECT * FROM agent_supply_components_v5 WHERE agent_id=? AND component_type='adapter' AND name=? AND source=?",(agent_id,NAME,SOURCE)).fetchone()
    if not row:return None
    try:
        value=core.load(row['metadata_json'],{})
        code=value['source_code']
        valid=(isinstance(code,str) and 0<len(code.encode())<=MAX_SOURCE_BYTES
               and hashlib.sha256(code.encode()).hexdigest()==row['sha256']
               and value['protocol']==PROTOCOL and type(value['enabled']) is bool
               and core.load(row['capabilities_json'])==[] and core.load(row['permissions_json'])=={})
        if not valid:raise ValueError()
        versions=history.load(value)
    except (ValueError,TypeError,KeyError):raise HTTPException(409,'Adapter registration integrity check failed') from None
    return {'row':dict(row),'source_code':code,'enabled':value['enabled'],'history':versions,'configuration_sha256':digest(dict(row))}


def public(value):
    return {'configured':bool(value),'configuration_sha256':value['configuration_sha256'] if value else None,
        'source_sha256':value['row']['sha256'] if value else None,
        'enabled':value['enabled'] if value else False,'protocol':PROTOCOL,
        'stored_source_versions':len(value['history']) if value else 0,'source_version_limit':history.LIMIT,
        'network_allowed':False,'tool_permissions':[],'execution_dispatched':False}

class AdapterPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_code:str=Field(min_length=1,max_length=65536)
    expected_configuration_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    enabled:StrictBool=False

class AdapterSave(AdapterPreview):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:StrictBool


def preview(db,agent_id,body):
    old=current(db,agent_id)
    if (old['configuration_sha256'] if old else None)!=body.expected_configuration_sha256:
        raise HTTPException(409,'Adapter configuration changed; reload and preview again')
    try:encoded=body.source_code.encode()
    except UnicodeError:raise HTTPException(422,'Adapter source must be valid UTF-8') from None
    if len(encoded)>MAX_SOURCE_BYTES:raise HTTPException(422,'Adapter source exceeds byte limit')
    try:ast.parse(body.source_code,filename='agent_adapter.py')
    except (SyntaxError,ValueError,RecursionError):raise HTTPException(422,'Adapter source is not valid Python syntax') from None
    try:version_count=history.capacity(core.load(old['row']['metadata_json']) if old else {},body.source_code)
    except ValueError as error:raise HTTPException(409,str(error)) from None
    result={'agent_id':agent_id,'stored_versions_after_save':version_count,'source_sha256':hashlib.sha256(encoded).hexdigest(),'source_bytes':len(encoded),
        'expected_configuration_sha256':body.expected_configuration_sha256,'enabled':body.enabled,
        'protocol':PROTOCOL,'network_allowed':False,'tool_permissions':[],'execution_dispatched':False,
        'source_trust':'operator_review_required','supported_scenario':'prompt-injection-canary.v1','supported_scenarios':['prompt-injection-canary.v1','memory-protected-policy.v1','synthetic-tool-request-boundary.v1'],'tool_protocol':TOOL_PROTOCOL}
    result['preview_hash']=digest(result)
    return result


@router.get('/{agent_id}')
def read(agent_id:str):
    with access(),core.connect() as db:return public(current(db,agent_id))


@router.post('/{agent_id}/preview')
def read_preview(agent_id:str,body:AdapterPreview):
    with access(),core.connect() as db:
        db.execute('BEGIN')
        return preview(db,agent_id,body)


@router.put('/{agent_id}')
def save(agent_id:str,body:AdapterSave):
    if not body.confirmed:raise HTTPException(422,'Confirm storing reviewed adapter code without execution')
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return save_in_transaction(db,agent_id,body)


def save_in_transaction(db,agent_id,body,event_type='agent_attack.adapter_registered'):
    reviewed=preview(db,agent_id,body)
    if reviewed['preview_hash']!=body.preview_hash:raise HTTPException(409,'Adapter preview changed; review again')
    now=core.utcnow();sha=reviewed['source_sha256']
    old=current(db,agent_id)
    archived=history.capture(core.load(old['row']['metadata_json']) if old else {},body.source_code,now)
    metadata=core.dump({'source_code':body.source_code,'enabled':body.enabled,'protocol':PROTOCOL,**archived})
    db.execute('''INSERT INTO agent_supply_components_v5
        (id,agent_id,component_type,name,version,source,sha256,capabilities_json,permissions_json,metadata_json,first_seen_at,last_seen_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(agent_id,component_type,name,source) DO UPDATE SET
        version=excluded.version,sha256=excluded.sha256,metadata_json=excluded.metadata_json,last_seen_at=excluded.last_seen_at''',
        (core.uid('adapter'),agent_id,'adapter',NAME,sha[:16],SOURCE,sha,'[]','{}',metadata,now,now))
    value=public(current(db,agent_id))
    db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
        ('agent_attack',None,agent_id,event_type,core.dump(value),now))
    return value

class AdapterState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_configuration_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    enabled:StrictBool
    confirmed:StrictBool


@router.put('/{agent_id}/state')
def set_state(agent_id:str,body:AdapterState):
    if not body.confirmed:raise HTTPException(422,'Confirm changing adapter readiness without execution')
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        value=current(db,agent_id)
        if not value or value['configuration_sha256']!=body.expected_configuration_sha256:
            raise HTTPException(409,'Adapter changed; reload before changing readiness')
        if value['enabled']!=body.enabled:
            metadata=core.load(value['row']['metadata_json']);metadata.update(history.capture(metadata,value['source_code'],core.utcnow()));metadata['enabled']=body.enabled
            db.execute('UPDATE agent_supply_components_v5 SET metadata_json=?,last_seen_at=? WHERE id=?',
                (core.dump(metadata),core.utcnow(),value['row']['id']))
            db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                ('agent_attack',None,agent_id,'agent_attack.adapter_state_changed',core.dump({'enabled':body.enabled,'source_sha256':value['row']['sha256']}),core.utcnow()))
        return public(current(db,agent_id))


class RestorePreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    expected_configuration_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


class RestoreSave(RestorePreview):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmed:StrictBool


def version_source(value,source_sha256):
    if value:
        for row in value['history']:
            if row['source_sha256']==source_sha256:return row
        if value['row']['sha256']==source_sha256:
            return {'source_sha256':source_sha256,'source_code':value['source_code'],
                'recorded_at':None,'origin':'legacy_current_only','record_sha256':value['configuration_sha256']}
    raise HTTPException(404,'Stored adapter source version is unavailable')


def component_public_metadata(value):
    """Generic inventories never return current or historical Python source."""
    return {'protocol':PROTOCOL,'enabled':value['enabled'],'source':SOURCE,
        'source_sha256':value['row']['sha256'],'stored_source_versions':len(value['history']),
        'source_history':history.summaries(core.load(value['row']['metadata_json']))}


@router.get('/{agent_id}/versions')
def read_versions(agent_id:str):
    with access(),core.connect() as db:
        value=current(db,agent_id)
        rows=history.summaries(core.load(value['row']['metadata_json'])) if value else []
        if value and not any(row['source_sha256']==value['row']['sha256'] for row in rows):
            rows.append({'source_sha256':value['row']['sha256'],'source_bytes':len(value['source_code'].encode()),
                'recorded_at':None,'origin':'legacy_current_only','record_sha256':None,'archived':False})
        for row in rows:row['current']=bool(value and row['source_sha256']==value['row']['sha256'])
        return {**public(value),'items':list(reversed(rows)),'history_is_complete_before_first_capture':False}


@router.get('/{agent_id}/versions/{source_sha256}/source')
def read_version_source(agent_id:str,source_sha256:str=Path(pattern=r'^[a-f0-9]{64}$')):
    with access(),core.connect() as db:
        db.execute('BEGIN');value=current(db,agent_id);row=version_source(value,source_sha256)
        return {'source_sha256':source_sha256,'source_code':row['source_code'],
            'configuration_sha256':value['configuration_sha256'],'executes_code':False}


def restore_plan(db,agent_id,body):
    value=current(db,agent_id)
    if not value or value['configuration_sha256']!=body.expected_configuration_sha256:
        raise HTTPException(409,'Adapter changed; reload before restoring a version')
    row=version_source(value,body.source_sha256)
    registration=AdapterPreview(source_code=row['source_code'],enabled=False,
        expected_configuration_sha256=body.expected_configuration_sha256)
    checked=preview(db,agent_id,registration)
    result={**checked,'operation':'restore_disabled','restored_record_sha256':row['record_sha256'],
        'requires_separate_enable_and_execution_review':True}
    result['preview_hash']=digest(result)
    return result,registration,checked['preview_hash']


@router.post('/{agent_id}/restore-preview')
def read_restore_preview(agent_id:str,body:RestorePreview):
    with access(),core.connect() as db:
        db.execute('BEGIN');return restore_plan(db,agent_id,body)[0]


@router.put('/{agent_id}/restore')
def restore_version(agent_id:str,body:RestoreSave):
    if not body.confirmed:raise HTTPException(422,'Review source and confirm restoring a disabled version')
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE');reviewed,registration,registration_hash=restore_plan(db,agent_id,body)
        if reviewed['preview_hash']!=body.preview_hash:raise HTTPException(409,'Restore proposal changed; review again')
        value=save_in_transaction(db,agent_id,AdapterSave(**registration.model_dump(),preview_hash=registration_hash,confirmed=True),
            event_type='agent_attack.adapter_restored_disabled')
        return {**value,'restored_source_sha256':body.source_sha256,'remains_disabled':True}
