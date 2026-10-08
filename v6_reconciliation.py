"""Explicit operator review of unknown consumption; never resumes or dispatches tasks."""
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import final_core as core
import v5_runtime as runtime
from v6_intents import LOCAL_SESSION_PRINCIPAL


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=200)
    confirmed: Literal[True]


class ReviewMaterial(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    call_id: str
    decision_id: str
    provider_id: str
    review_kind: Literal['operator_review', 'provider_record']
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_micros: int = Field(ge=0)
    runtime_ms: int = Field(ge=0)


def reconcile(call_id: str, body: ReviewInput):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        call = db.execute('SELECT c.*,t.run_id FROM runtime_calls c JOIN agent_tasks t ON t.id=c.task_id '
                          'WHERE c.id=?', (call_id,)).fetchone()
        if not call:
            raise HTTPException(404, 'Model call not found')
        if not call['run_id']:
            raise HTTPException(409, 'Reconciliation requires a recorded Run')
        artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=? AND kind='runtime.usage_review'",
                              (body.artifact_id, call['run_id'])).fetchone()
        try:
            raw = Path(artifact['uri']).read_bytes() if artifact else None
            if raw is None or hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                raise ValueError('integrity')
            material = ReviewMaterial.model_validate_json(raw)
        except (OSError, ValueError, ValidationError):
            raise HTTPException(409, 'Usage review evidence is missing, changed or invalid')
        if (material.call_id, material.decision_id, material.provider_id) != (
                call_id, call['decision_id'], call['provider_id']):
            raise HTTPException(409, 'Usage review belongs to another call or provider')
        existing = db.execute('SELECT * FROM model_usage_reconciliations_v6 WHERE call_id=?', (call_id,)).fetchone()
        if existing:
            if existing['artifact_id'] != body.artifact_id or existing['artifact_sha256'] != artifact['sha256']:
                raise HTTPException(409, 'Model call was already reconciled with another review')
            return dict(existing)
        if call['state'] != 'unknown':
            raise HTTPException(409, 'Only unknown model consumption can be reconciled')
        usage = runtime.record_usage_in_transaction(db, runtime.UsageReport(
            decision_id=call['decision_id'], call_id=call_id, idempotency_key=f'v6-reconciliation:{call_id}',
            input_tokens=material.input_tokens, output_tokens=material.output_tokens,
            cost_micros=material.cost_micros, runtime_ms=material.runtime_ms))
        db.execute('INSERT INTO model_usage_reconciliations_v6 VALUES(?,?,?,?,?,?,?,?,?)',
                   (call_id, call['task_id'], call['run_id'], body.artifact_id, artifact['sha256'],
                    usage['id'], material.review_kind, LOCAL_SESSION_PRINCIPAL, core.utcnow()))
        runtime._emit(db, call_id, 'call.reconciled', {'artifact_id':body.artifact_id,
            'artifact_sha256':artifact['sha256'], 'usage_id':usage['id'],
            'principal_id':LOCAL_SESSION_PRINCIPAL, 'review_kind':material.review_kind}, call['campaign_id'])
        return dict(db.execute('SELECT * FROM model_usage_reconciliations_v6 WHERE call_id=?', (call_id,)).fetchone())
