"""Read-only V6 compatibility endpoints over the existing runtime state."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query

import final_core
from v6_runtime_bridge import list_events, list_model_call_snapshots, list_runners

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])


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
            result.append(value)
        return {'runs': result}
