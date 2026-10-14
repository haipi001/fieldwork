"""Explicit local operator review; never overrides domain or execution gates."""
import hashlib
import json
import math
import uuid
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool
import final_core as core
from reporting import redact_structure
from v6_intents import LOCAL_SESSION_PRINCIPAL

router=APIRouter(prefix='/api/v1/v6',tags=['V6 Control Plane'])


def digest(row):
    return hashlib.sha256(json.dumps(dict(row),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def approval(db,grant,intent):
    row=db.execute('SELECT * FROM capability_approvals_v6 WHERE grant_id=?',(grant['id'],)).fetchone()
    if row is None:return None
    if (row['intent_id']!=intent['id'] or row['principal_id']!=LOCAL_SESSION_PRINCIPAL
        or row['grant_sha256']!=digest(grant) or row['intent_sha256']!=digest(intent)):
        return {'decision':'invalid'}
    return dict(row)


def preview(db,grant_id):
    from v6_capabilities import capability_matches
    grant=db.execute('SELECT * FROM capability_grants_v6 WHERE id=?',(grant_id,)).fetchone()
    if grant is None:raise KeyError(grant_id)
    intent=db.execute('SELECT * FROM action_intents_v6 WHERE id=?',(grant['intent_id'],)).fetchone()
    if intent is None:raise ValueError('Intent not found')
    eligible,reason=capability_matches(db,intent['id'],grant_id,principal_id=intent['principal_id'])
    identity_supported=intent['principal_id']==LOCAL_SESSION_PRINCIPAL
    if intent['principal_binding']=='first_party_worker_code_path' and grant['issued_by']=='fieldwork:control-plane':
        binding=db.execute('SELECT 1 FROM model_gateway_bindings_v6 WHERE grant_id=? AND intent_id=? AND principal_id=?',
                           (grant_id,intent['id'],intent['principal_id'])).fetchone()
        identity_supported=bool(binding)
    risk=intent['risk_hint']
    risk_ok=risk is None or (isinstance(risk,(int,float)) and math.isfinite(risk) and 0<=risk<80)
    operation_supported=(intent['capability'] in {'network.request','browser.navigate'} and intent['operation'] in {'read','get','navigate'}) or (intent['capability']=='model.call' and intent['operation']=='call')
    supported=(reason in {'eligible_only','approval_required'} and not intent['side_effect'] and risk_ok and operation_supported and identity_supported)
    existing=approval(db,grant,intent)
    used=db.execute('SELECT COUNT(*) FROM capability_uses_v6 WHERE grant_id=?',(grant_id,)).fetchone()[0]
    value={'grant':dict(grant),'intent':dict(intent),'eligible':eligible,'reason_code':reason,
           'can_approve':bool(supported and existing is None),'can_reject':existing is None,
           'approval':existing,'used_count':used,'approval_risk_ceiling_exclusive':80,
           'execution_started':False}
    value['preview_sha256']=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return value


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision: str=Field(pattern=r'^(approve|reject)$')
    preview_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool
    reason: str=Field(min_length=3,max_length=2000)


def decide(db,grant_id,body):
    if not body.confirmed or len(body.reason.strip())<3:raise ValueError('Explicit confirmation and reason required')
    db.execute('BEGIN IMMEDIATE')
    current=preview(db,grant_id)
    if current['preview_sha256']!=body.preview_sha256:raise ValueError('Review changed; reload before deciding')
    if current['approval'] is not None:raise ValueError('Grant already reviewed')
    if body.decision=='approve' and not current['can_approve']:raise ValueError('Domain, context or risk prevents approval')
    grant,intent=current['grant'],current['intent']
    identity='approval-'+uuid.uuid4().hex;now=core.utcnow()
    db.execute('INSERT INTO capability_approvals_v6 VALUES(?,?,?,?,?,?,?,?,?,?)',
               (identity,grant_id,intent['id'],LOCAL_SESSION_PRINCIPAL,body.decision,body.preview_sha256,
                digest(grant),digest(intent),body.reason,now))
    db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
               ('control',grant['campaign_id'],grant['task_id'],'capability.reviewed',json.dumps({'approval_id':identity,'grant_id':grant_id,'intent_id':intent['id'],'decision':body.decision}),now))
    return {'id':identity,'grant_id':grant_id,'decision':body.decision,'executed':False}


@router.get('/capability-grants/{grant_id}/approval-preview')
def approval_preview(grant_id:str):
    try:
        with core.connect() as db:return redact_structure(preview(db,grant_id))
    except KeyError:raise HTTPException(404,'Grant not found') from None
    except (ValueError,TypeError):raise HTTPException(409,'Grant review cannot be verified') from None


@router.post('/capability-grants/{grant_id}/approval')
def record_approval(grant_id:str,body:Decision):
    try:
        with core.connect() as db:return decide(db,grant_id,body)
    except KeyError:raise HTTPException(404,'Grant not found') from None
    except (ValueError,TypeError):raise HTTPException(409,'Review changed or approval blocked; reload') from None
