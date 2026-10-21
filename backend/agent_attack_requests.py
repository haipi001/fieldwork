"""Reviewed attack-test requests. No executable adapter is installed by registration."""
import hashlib
import json
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
import final_core as core

router=APIRouter(prefix='/agent-attack-requests',tags=['Agent attack requests'])

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    agent_id:str=Field(min_length=1,max_length=200)
    category:Literal['injection','tool','memory']
    max_cases:int=Field(strict=True,ge=1,le=100)

class Commit(Preview):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    request_key:str=Field(min_length=8,max_length=100)
    confirmed:StrictBool

class Cancel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def preview(db,body):
    row=db.execute('SELECT * FROM agent_registry_v5 WHERE id=?',(body.agent_id,)).fetchone()
    if not row:raise HTTPException(404,'Registered agent not found')
    components=[dict(x) for x in db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=? ORDER BY id',(body.agent_id,))]
    from agent_attack_adapters import current,public
    adapter=public(current(db,body.agent_id))
    available=bool(adapter['configured'] and adapter['enabled'] and body.category in ('injection','memory','tool') and body.max_cases>=2)
    blocker=('agent_execution_adapter_not_configured' if not adapter['enabled'] else
             ('local_scenario_not_supported' if body.category not in ('injection','memory','tool') else
              ('two_case_budget_required' if body.max_cases<2 else 'execution_plan_review_required')))
    result={**body.model_dump(),'agent_name':row['name'],'agent_status':row['status'],
        'agent_snapshot_sha256':digest(dict(row)),'component_snapshot_sha256':digest(components),
        'contract_version':1,'mode':'reviewed_request','execution_supported':False,
        'blockers':[blocker],'adapter':adapter,'execution_plan_available':available,
        'next_action':'Review the local adapter and execution dispatch readiness; this request does not dispatch automatically.',
        'network_requests':0,'model_calls':0,'agent_tested':False}
    result['preview_hash']=digest(result)
    return result


@router.post('/preview')
def read_preview(body:Preview):
    with core.connect() as db:
        db.execute('BEGIN')
        return preview(db,body)


@router.post('',status_code=201)
def create(body:Commit):
    if not body.confirmed:raise HTTPException(422,'Confirm saving a blocked request without dispatch')
    request_id='attack-request-'+digest({'agent_id':body.agent_id,'request_key':body.request_key})[:32]
    inputs=Preview(**body.model_dump(include={'agent_id','category','max_cases'}))
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(request_id,)).fetchone()
        if existing:
            payload=core.load(existing['result_json'],{})
            if payload.get('preview_hash')!=body.preview_hash or any(payload.get(k)!=v for k,v in inputs.model_dump().items()):
                raise HTTPException(409,'Request key already has different contents')
            return {'id':request_id,'status':existing['status'],'created':False,'agent_tested':False}
        current=preview(db,inputs)
        if current['preview_hash']!=body.preview_hash:raise HTTPException(409,'Agent or components changed; preview again')
        status='awaiting_execution_review' if current['execution_plan_available'] else ('awaiting_adapter' if not current['adapter']['enabled'] else 'blocked')
        db.execute('INSERT INTO agent_attack_tests_v5 VALUES(?,?,?,?,?,?,?,?,?)',
            (request_id,body.agent_id,body.category,'reviewed_request',status,core.dump(current),'[]',core.utcnow(),None))
        db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
            ('agent_attack',None,request_id,'agent_attack.request_saved',core.dump({'preview_hash':body.preview_hash,'status':status}),core.utcnow()))
        return {'id':request_id,'status':status,'created':True,'agent_tested':False}


@router.post('/{request_id}/cancel')
def cancel(request_id:str,body:Cancel):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(request_id,)).fetchone()
        if not row:raise HTTPException(404,'Attack request not found')
        if row['mode']!='reviewed_request' or core.load(row['result_json'],{}).get('preview_hash')!=body.preview_hash:
            raise HTTPException(409,'Request changed; refresh')
        if row['status'] not in ('awaiting_adapter','awaiting_execution_review','blocked','cancelled'):raise HTTPException(409,'Request is no longer cancellable')
        if row['status']!='cancelled':
            db.execute("UPDATE agent_attack_tests_v5 SET status='cancelled',completed_at=? WHERE id=?",(core.utcnow(),request_id))
            db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                ('agent_attack',None,request_id,'agent_attack.request_cancelled','{}',core.utcnow()))
        return {'id':request_id,'status':'cancelled','agent_tested':False}
