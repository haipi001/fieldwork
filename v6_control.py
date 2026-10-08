"""Read-only V6 compatibility endpoints over the existing runtime state."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

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
