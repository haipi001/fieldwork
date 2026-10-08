"""Read-only V6 compatibility endpoints over the existing runtime state."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Literal

import final_core
from v6_runtime_bridge import list_events, list_model_call_snapshots, list_runners

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])


class ContainmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason_code: Literal['compromised', 'operator_containment']


@router.post('/runs/{run_id}/contain')
def contain_run(run_id: str, body: ContainmentInput):
    from v6_intents import LOCAL_SESSION_PRINCIPAL
    with final_core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM analysis_runs WHERE id=?', (run_id,)).fetchone():
            raise HTTPException(404, 'Run not found')
        now = final_core.utcnow()
        db.execute('INSERT OR IGNORE INTO run_containment_v6 VALUES(?,?,?,?)',
                   (run_id, body.reason_code, LOCAL_SESSION_PRINCIPAL, now))
        db.execute('INSERT OR IGNORE INTO capability_revocations_v6 '
                   'SELECT id,?,?,? FROM capability_grants_v6 WHERE run_id=?',
                   (LOCAL_SESSION_PRINCIPAL, now,
                    'compromised' if body.reason_code == 'compromised' else 'operator_revoked', run_id))
        return {'containment': dict(db.execute('SELECT * FROM run_containment_v6 WHERE run_id=?',
                                               (run_id,)).fetchone()),
                'boundary': 'subsequent_v6_authorized_operations',
                'revoked_grants': db.execute('SELECT COUNT(*) FROM capability_revocations_v6 r '
                    'JOIN capability_grants_v6 g ON g.id=r.grant_id WHERE g.run_id=?', (run_id,)).fetchone()[0]}


@router.get('/packs')
def packs():
    from v6_packs import list_packs
    return {'packs': list_packs()}


@router.get("/campaigns/{campaign_id}/runtime-events")
def campaign_runtime_events(campaign_id: str, after_id: int = Query(0, ge=0),
                            limit: int = Query(100, ge=1, le=500)):
    with final_core.connect() as db:
        return {"events": list_events(db, after_id=after_id, limit=limit, campaign_id=campaign_id)}


@router.get("/tasks/{task_id}/model-call-snapshots")
def task_model_call_snapshots(task_id: str):
    with final_core.connect() as db:
        return {"calls": list_model_call_snapshots(db, task_id=task_id)}


@router.get("/runners")
def runners():
    with final_core.connect() as db:
        return {"runners": list_runners(db)}


@router.get('/eval-scenarios')
def eval_scenarios():
    with final_core.connect() as db:
        return {'scenarios': [json.loads(row['manifest_json']) for row in db.execute(
            'SELECT manifest_json FROM eval_scenarios_v6 ORDER BY id,version')]}


@router.get('/eval-runs')
def eval_runs(limit: int = Query(100, ge=1, le=500)):
    with final_core.connect() as db:
        result = []
        for row in db.execute('SELECT * FROM eval_runs_v6 ORDER BY created_at DESC,id LIMIT ?', (limit,)):
            value = dict(row)
            for name in ('subject', 'metrics', 'result'):
                value[name] = json.loads(value.pop(name + '_json'))
            artifact = db.execute('SELECT uri,sha256 FROM artifacts WHERE id=? AND run_id=?',
                                  (row['artifact_id'], row['run_id'])).fetchone()
            scenario = db.execute('SELECT manifest_json,manifest_sha256 FROM eval_scenarios_v6 '
                                  'WHERE id=? AND version=?',
                                  (row['scenario_id'], row['scenario_version'])).fetchone()
            try:
                artifact_valid = bool(artifact and artifact['sha256'] == row['artifact_sha256']
                    and hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest() == row['artifact_sha256'])
            except OSError:
                artifact_valid = False
            scenario_valid = bool(scenario and hashlib.sha256(scenario['manifest_json'].encode()).hexdigest()
                                  == scenario['manifest_sha256'])
            value['integrity'] = {'artifact': artifact_valid, 'scenario': scenario_valid}
            value['status'] = ('passed' if value['result']['passed'] else 'failed') if artifact_valid and scenario_valid else 'invalid'
            result.append(value)
        return {'runs': result}
