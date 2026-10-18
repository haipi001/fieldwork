"""Read-only V6 compatibility endpoints over the existing runtime state."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Literal

import final_core
from v6_reconciliation import ReviewInput, ReviewMaterial
from v6_runtime_bridge import list_events, list_model_call_snapshots, list_runners

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])


class ContainmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason_code: Literal['compromised', 'operator_containment']


class ReleaseInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_generation: int
    artifact_id: str


@router.post('/runs/{run_id}/contain')
def contain_run(run_id: str, body: ContainmentInput):
    from v6_intents import LOCAL_SESSION_PRINCIPAL
    from v6_containment import containment_state
    with final_core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM analysis_runs WHERE id=?', (run_id,)).fetchone():
            raise HTTPException(404, 'Run not found')
        now = final_core.utcnow()
        if not containment_state(db, run_id)['active']:
            db.execute('INSERT INTO run_containment_events_v6(run_id,state,reason_code,principal_id,created_at) '
                       'VALUES(?,?,?,?,?)', (run_id, 'contained', body.reason_code, LOCAL_SESSION_PRINCIPAL, now))
        db.execute('INSERT OR IGNORE INTO run_containment_v6 VALUES(?,?,?,?)',
                   (run_id, body.reason_code, LOCAL_SESSION_PRINCIPAL, now))
        db.execute('INSERT OR IGNORE INTO capability_revocations_v6 '
                   'SELECT id,?,?,? FROM capability_grants_v6 WHERE run_id=?',
                   (LOCAL_SESSION_PRINCIPAL, now,
                    'compromised' if body.reason_code == 'compromised' else 'operator_revoked', run_id))
        record = db.execute('SELECT * FROM run_containment_events_v6 WHERE run_id=? ORDER BY id DESC LIMIT 1',
                            (run_id,)).fetchone() or db.execute('SELECT * FROM run_containment_v6 WHERE run_id=?', (run_id,)).fetchone()
        return {'containment': dict(record),
                'boundary': 'subsequent_v6_authorized_operations',
                **containment_state(db, run_id),
                'revoked_grants': db.execute('SELECT COUNT(*) FROM capability_revocations_v6 r '
                    'JOIN capability_grants_v6 g ON g.id=r.grant_id WHERE g.run_id=?', (run_id,)).fetchone()[0]}


@router.post('/runs/{run_id}/release-containment')
def release_containment(run_id: str, body: ReleaseInput):
    from v6_intents import LOCAL_SESSION_PRINCIPAL
    from v6_containment import containment_state
    with final_core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = containment_state(db, run_id)
        if not current['active'] or current['generation'] != body.expected_generation:
            raise HTTPException(409, 'Containment state changed')
        run = db.execute('SELECT r.*,e.current_scope_snapshot_id,e.current_policy_id,e.status engagement_status,s.confirmed_at '
            'FROM analysis_runs r JOIN engagements_v2 e ON e.id=r.engagement_id '
            'JOIN scope_snapshots s ON s.id=r.scope_snapshot_id WHERE r.id=?', (run_id,)).fetchone()
        if (not run or run['engagement_status'] != 'ready' or not run['confirmed_at']
                or run['scope_snapshot_id'] != run['current_scope_snapshot_id'] or run['policy_id'] != run['current_policy_id']):
            raise HTTPException(409, 'Run authority is stale')
        if db.execute("SELECT 1 FROM runtime_calls c JOIN agent_tasks t ON t.id=c.task_id WHERE t.run_id=? "
                      "AND c.state IN ('reserved','calling','unknown') LIMIT 1", (run_id,)).fetchone():
            raise HTTPException(409, 'Run has active or unresolved model consumption')
        if db.execute('SELECT 1 FROM http_gateway_executions_v6 x JOIN agent_tasks t ON t.id=x.task_id '
                      'LEFT JOIN http_gateway_receipts_v6 r ON r.action_id=x.action_id '
                      'WHERE t.run_id=? AND r.action_id IS NULL LIMIT 1', (run_id,)).fetchone():
            raise HTTPException(409, 'Run has an active or unresolved read execution')
        if db.execute("SELECT 1 FROM agent_incidents i JOIN candidate_findings c ON c.id=i.candidate_id WHERE c.run_id=? "
            "AND COALESCE((SELECT state FROM incident_response_events_v6 WHERE candidate_id=c.id ORDER BY id DESC LIMIT 1),'DETECTED') "
            "NOT IN ('RECOVERED','CLOSED') LIMIT 1", (run_id,)).fetchone():
            raise HTTPException(409, 'Run has unresolved incident response')
        from v6_incidents import _response_evidence_intact
        response_events = db.execute('SELECT e.* FROM incident_response_events_v6 e '
            'JOIN agent_incidents i ON i.candidate_id=e.candidate_id '
            'JOIN candidate_findings c ON c.id=i.candidate_id WHERE c.run_id=?', (run_id,)).fetchall()
        if any(event['run_id'] != run_id or not _response_evidence_intact(db, event)
               for event in response_events):
            raise HTTPException(409, 'Run incident response evidence is missing or changed')
        artifact = db.execute('SELECT uri,sha256 FROM artifacts WHERE id=? AND run_id=?', (body.artifact_id, run_id)).fetchone()
        try:
            intact = artifact and hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest() == artifact['sha256']
        except OSError:
            intact = False
        if not intact:
            raise HTTPException(409, 'Release review evidence is missing or changed')
        db.execute('INSERT INTO run_containment_events_v6(run_id,state,reason_code,artifact_id,artifact_sha256,principal_id,created_at) '
                   'VALUES(?,?,?,?,?,?,?)', (run_id, 'released', 'operator_reviewed', body.artifact_id,
                                           artifact['sha256'], LOCAL_SESSION_PRINCIPAL, final_core.utcnow()))
        return {**containment_state(db, run_id), 'previous_grants_remain_revoked': True}


@router.get('/packs')
def packs():
    from v6_packs import list_packs
    return {'packs': list_packs()}


@router.get('/runtime-summary')
def runtime_summary():
    from v6_containment import is_contained
    with final_core.connect() as db:
        db.execute('BEGIN')
        def states(table, column='status'):
            counts = {row['state']: row['count'] for row in db.execute(
                f'SELECT {column} state,COUNT(*) count FROM {table} GROUP BY {column}')}
            return {'total': sum(counts.values()), 'states': counts}
        loads = [dict(row) for row in db.execute("SELECT r.id,r.status,r.max_concurrency,"
            "COUNT(t.id) running_tasks FROM runner_registry_v5 r LEFT JOIN agent_tasks t "
            "ON t.lease_owner=r.id AND t.status='running' GROUP BY r.id ORDER BY r.id")]
        containment_runs = {row[0] for row in db.execute('SELECT run_id FROM run_containment_v6 UNION '
                                                         'SELECT run_id FROM run_containment_events_v6')}
        return {'snapshot_at': final_core.utcnow(),
                'groups': states('research_groups'), 'tasks': states('agent_tasks'),
                'task_roles': {row['role']: row['count'] for row in db.execute(
                    'SELECT role,COUNT(*) count FROM agent_tasks GROUP BY role')},
                'runners': states('runner_registry_v5'), 'runner_load': loads,
                'model_calls': states('runtime_calls', 'state'),
                'http_receipts': states('http_gateway_receipts_v6'),
                'http_unsettled': db.execute('SELECT COUNT(*) FROM http_gateway_executions_v6 x '
                    'LEFT JOIN http_gateway_receipts_v6 r ON r.action_id=x.action_id WHERE r.action_id IS NULL').fetchone()[0],
                'contained_runs': sum(is_contained(db, run) for run in containment_runs),
                'event_count': db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0],
                'grants': {'issued': db.execute('SELECT COUNT(*) FROM capability_grants_v6').fetchone()[0],
                           'revoked': db.execute('SELECT COUNT(*) FROM capability_revocations_v6').fetchone()[0],
                           'uses': db.execute('SELECT COUNT(*) FROM capability_uses_v6').fetchone()[0]}}


@router.get('/runs/{run_id}/evidence-lineage')
def evidence_lineage(run_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with final_core.connect() as db:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM analysis_runs WHERE id=?', (run_id,)).fetchone():
            raise HTTPException(404, 'Run not found')
        rows = db.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at DESC,id LIMIT ? OFFSET ?',
                          (run_id, limit + 1, offset)).fetchall()
        items = []
        for artifact in rows[:limit]:
            try:
                intact = bool(artifact['sha256'] and hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest() == artifact['sha256'])
            except OSError:
                intact = False
            actions = [dict(row) for row in db.execute('SELECT x.action_id,x.task_id,x.runner_id,a.agent_id,a.principal_id '
                'FROM runtime_artifact_links_v6 l JOIN http_gateway_executions_v6 x ON x.action_id=l.action_id '
                'JOIN action_intents_v6 a ON a.id=x.intent_id JOIN agent_tasks t ON t.id=x.task_id '
                'WHERE l.artifact_id=? AND t.run_id=? ORDER BY x.started_at,x.action_id', (artifact['id'], run_id))]
            events = [f"v5:{row['id']}" for row in db.execute('SELECT e.id FROM v5_events e '
                'JOIN runtime_artifact_links_v6 l ON l.action_id=e.entity_id '
                'JOIN http_gateway_executions_v6 x ON x.action_id=l.action_id JOIN agent_tasks t ON t.id=x.task_id '
                "WHERE l.artifact_id=? AND t.run_id=? AND e.event_type='tool.execution.completed' ORDER BY e.id", (artifact['id'], run_id))]
            observations = [dict(row) for row in db.execute('SELECT id,observation_type,source_capability,created_at '
                'FROM observations WHERE raw_ref=? AND run_id=? ORDER BY created_at,id', (artifact['id'], run_id))]
            evidence = []
            for row in db.execute('SELECT e.id,e.observation_id,e.evidence_type,e.polarity,e.created_at FROM evidence_v2 e '
                'LEFT JOIN observations o ON o.id=e.observation_id AND o.run_id=e.run_id '
                'WHERE e.run_id=? AND (e.artifact_id=? OR o.raw_ref=?) ORDER BY e.created_at,e.id',
                (run_id, artifact['id'], artifact['id'])):
                value = dict(row)
                value['canonical_polarity'] = {'supporting':'support','counterevidence':'counter','context':'context',
                    'support':'support','counter':'counter'}.get(row['polarity'])
                evidence.append(value)
            items.append({'artifact_id':artifact['id'],'kind':artifact['kind'],'sha256':artifact['sha256'],
                'media_type':artifact['media_type'],'redacted':bool(artifact['redacted']),'created_at':artifact['created_at'],
                'integrity': 'intact' if intact else 'missing_or_changed', 'actions':actions,
                'runtime_event_ids':events,'observations':observations,'evidence':evidence})
        return {'run_id':run_id,'items':items,'offset':offset,'has_more':len(rows)>limit}


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


@router.post('/model-calls/{call_id}/reconcile')
def reconcile_model_call(call_id: str, body: ReviewInput):
    from v6_reconciliation import reconcile
    return reconcile(call_id, body)


@router.get('/model-usage-reconciliations')
def model_usage_reconciliations(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    from v6_reconciliation import read_reconciliations
    return read_reconciliations(limit, offset)


@router.post('/model-calls/{call_id}/usage-review', status_code=201)
def import_model_usage_review(call_id: str, body: ReviewMaterial):
    from v6_reconciliation import import_review
    return import_review(call_id, body)
