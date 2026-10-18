"""Explicit operator review of unknown consumption; never resumes or dispatches tasks."""
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import final_core as core
import v5_runtime as runtime
from v6_intents import LOCAL_SESSION_PRINCIPAL


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1, max_length=200)
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('confirmation must be the JSON boolean true')
        return value


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
        if not call['run_id'] or not db.execute('SELECT 1 FROM analysis_runs WHERE id=?',
                                               (call['run_id'],)).fetchone():
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


def read_reconciliations(limit=50, offset=0):
    with core.connect() as db:
        db.execute('BEGIN')
        rows = db.execute('SELECT * FROM model_usage_reconciliations_v6 ORDER BY created_at DESC,call_id '
                          'LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
        items = []
        for row in rows[:limit]:
            value = dict(row)
            artifact = db.execute('SELECT * FROM artifacts WHERE id=? AND run_id=?',
                                  (row['artifact_id'], row['run_id'])).fetchone()
            call = db.execute('SELECT c.*,t.run_id current_run_id FROM runtime_calls c '
                'JOIN agent_tasks t ON t.id=c.task_id WHERE c.id=?', (row['call_id'],)).fetchone()
            usage = db.execute('SELECT * FROM runtime_usage WHERE id=?', (row['usage_id'],)).fetchone()
            timing = db.execute('SELECT runtime_ms FROM runtime_call_timings WHERE call_id=?',
                                (row['call_id'],)).fetchone()
            report = db.execute('SELECT * FROM runtime_usage_reports WHERE usage_id=?',
                                (row['usage_id'],)).fetchone()
            valid = False
            try:
                raw = Path(artifact['uri']).read_bytes() if artifact else None
                material = ReviewMaterial.model_validate_json(raw) if raw else None
                valid = bool(material and call and usage and timing and report
                    and artifact['kind'] == 'runtime.usage_review'
                    and artifact['sha256'] == row['artifact_sha256']
                    and hashlib.sha256(raw).hexdigest() == row['artifact_sha256']
                    and report['decision_id'] == call['decision_id']
                    and report['idempotency_key'] == f"v6-reconciliation:{row['call_id']}"
                    and call['state'] == 'settled' and call['usage_id'] == row['usage_id']
                    and call['task_id'] == row['task_id'] and call['current_run_id'] == row['run_id']
                    and material.call_id == row['call_id'] and material.decision_id == call['decision_id']
                    and material.provider_id == call['provider_id'] and material.review_kind == row['review_kind']
                    and usage['task_id'] == row['task_id'] and usage['provider_id'] == call['provider_id']
                    and (material.input_tokens, material.output_tokens, material.cost_micros, material.runtime_ms)
                        == (usage['input_tokens'], usage['output_tokens'], usage['cost_micros'], timing['runtime_ms']))
            except (OSError, ValueError, ValidationError):
                pass
            value['integrity'] = 'intact' if valid else 'missing_or_changed'
            value['usage'] = ({key:usage[key] for key in ('input_tokens','output_tokens','cost_micros')}
                              if usage else None)
            value['runtime_ms'] = timing['runtime_ms'] if timing else None
            value['reservation_exceeded'] = ({
                'tokens': usage['input_tokens'] + usage['output_tokens'] > call['reserved_tokens'],
                'cost': usage['cost_micros'] > call['reserved_cost_micros'],
                'runtime': timing['runtime_ms'] > call['max_runtime_ms'] if timing else None,
            } if call and usage else None)
            items.append(value)
        return {'items':items, 'offset':offset, 'has_more':len(rows)>limit}


def import_review(call_id: str, material: ReviewMaterial):
    import json
    import uuid
    path = None
    created = False
    try:
        with core.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            call = db.execute('SELECT c.*,t.run_id FROM runtime_calls c JOIN agent_tasks t ON t.id=c.task_id '
                              'WHERE c.id=?', (call_id,)).fetchone()
            if not call:
                raise HTTPException(404, 'Model call not found')
            if call['state'] != 'unknown' or not call['run_id'] or not db.execute(
                    'SELECT 1 FROM analysis_runs WHERE id=?', (call['run_id'],)).fetchone():
                raise HTTPException(409, 'Review import requires unknown consumption with a recorded Run')
            if (material.call_id, material.decision_id, material.provider_id) != (
                    call_id, call['decision_id'], call['provider_id']):
                raise HTTPException(409, 'Usage review belongs to another call or provider')
            artifact_id = 'usage-review-' + uuid.uuid4().hex
            directory = Path(core.LOCAL_DATA_ROOT) / 'v6-usage-reviews'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (artifact_id + '.json')
            raw = json.dumps(material.model_dump(), sort_keys=True, separators=(',', ':')).encode()
            with path.open('xb') as output:
                created = True
                output.write(raw)
            digest = hashlib.sha256(raw).hexdigest()
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                       (artifact_id, call['run_id'], 'runtime.usage_review', str(path), digest,
                        'application/json', 1, core.utcnow()))
            runtime._emit(db, call_id, 'call.review_imported', {'artifact_id':artifact_id,
                'artifact_sha256':digest, 'principal_id':LOCAL_SESSION_PRINCIPAL}, call['campaign_id'])
        return {'artifact_id':artifact_id, 'sha256':digest, 'run_id':call['run_id'],
                'call_id':call_id, 'consumption_state':'unknown'}
    except Exception:
        if created and path is not None:
            path.unlink(missing_ok=True)
        raise
