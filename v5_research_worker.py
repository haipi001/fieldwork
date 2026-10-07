"""Explicit, bounded team analysis of recorded graph inputs; outputs remain research drafts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import ipaddress
import threading
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import Field

import v5_workers as w
import v5_runtime as r
from v5_graph import _bridge_node, _bridge_edge
from v5_orchestration import _owned_running, _check_lease_attempt, _continuous_scope_current, _group_can_lease, _sync_runner_jobs, recover_expired_leases
from v5_runtime_calls import reserve_call, start_call, fail_call, recover_calls

router = APIRouter(prefix='/api/v1/workers/research', tags=['V5 Research Worker'])
RUNNER_ID = f'builtin-research-{uuid.uuid4().hex[:12]}'
_jobs: dict[str, threading.Thread] = {}
_lock = threading.Lock()
_stopping = threading.Event()


class Hypothesis(w.StrictModel):
    statement: str = Field(min_length=10, max_length=500)
    scope: str = Field(min_length=3, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class ResearchOutput(w.StrictModel):
    summary: str = Field(min_length=5, max_length=2000)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=5)
    open_questions: list[str] = Field(default_factory=list, max_length=10)


def _inputs(db, task):
    group = db.execute("SELECT status FROM research_groups WHERE id=?", (task['group_id'],)).fetchone()
    if not group or group['status'] != 'active':
        raise HTTPException(409, 'research group is not active')
    if not _continuous_scope_current(db, task):
        raise HTTPException(409, 'research authority or run changed')
    hashes = w._load(task['context_capsule_json'], {}).get('research_input_hashes')
    if not isinstance(hashes, dict) or len(hashes) > 20:
        raise HTTPException(409, 'research inputs are not frozen; recreate or explicitly start the team')
    rows = []
    for node_id, digest in hashes.items():
        row = db.execute('SELECT * FROM research_nodes WHERE id=? AND campaign_id=?', (node_id, task['campaign_id'])).fetchone()
        if not row or w._node_digest(row) != digest:
            raise HTTPException(409, 'research input graph changed')
        rows.append(row)
    return rows


def _provider(decision):
    with w._core().connect() as db:
        row = db.execute("SELECT * FROM runtime_providers WHERE id=? AND enabled=1 AND last_health='healthy'",
                         (decision['provider_ref'],)).fetchone()
    if not row or r._provider_config_hash(row) != decision['provider_configuration_sha256']:
        raise HTTPException(409, 'research provider unavailable or configuration changed')
    parsed = urlsplit(row['base_url'])
    try:
        supported = (parsed.scheme == 'https' if decision['route'] == 'cloud' else
                     parsed.scheme == 'http' and parsed.port and ipaddress.ip_address(parsed.hostname).is_loopback and not row['secret_ref'])
    except ValueError:
        supported = False
    if not supported:
        raise HTTPException(409, 'research provider endpoint is not supported')
    if decision['route'] == 'cloud' and r._load(row['metadata_json'], {}).get('cost_micros_per_million_tokens', 0) <= 0:
        raise HTTPException(409, 'cloud research requires a positive configured token cost bound')
    if decision['route'] == 'cloud' and row['secret_ref']:
        import runtime_secrets
        key = runtime_secrets.get(row['secret_ref'])
        if not key or any(ch in key for ch in '\r\n\x00'):
            raise HTTPException(409, 'cloud model credential unavailable')
    return row


def _route(task):
    capsule = w._load(task['context_capsule_json'], {})
    approved = capsule.get('cloud_context_approved') is True
    decision = r.route_task(r.RouteRequest(task_id=task['id'], task_type=f"team_{task['role']}",
        sensitivity='public' if approved else 'secret', mode=None if approved else 'local',
        budget_remaining_micros=w._load(task['budget_json'], {}).get('max_cost_micros', 0),
        estimated_tokens=w._load(task['budget_json'], {}).get('max_tokens', 8000)))
    if decision['status'] != 'selected' or decision['route'] not in {'local','cloud'}:
        raise HTTPException(409, 'research profile has no permitted healthy route')
    return decision


def _claim(group_id):
    if _stopping.is_set():
        return None
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        group = db.execute("SELECT * FROM research_groups WHERE id=? AND status='active'", (group_id,)).fetchone()
        if not group:
            return None
        now = w._now()
        runner = db.execute('SELECT * FROM runner_registry_v5 WHERE id=?', (RUNNER_ID,)).fetchone()
        if not runner:
            db.execute('INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                RUNNER_ID, 'Built-in local research worker', 'research-worker', 'online',
                w._dump(['recorded_graph_research']), w._dump({'location': 'local'}), 32, 0,
                now, w._dump({'builtin': True}), now, now))
        db.execute("UPDATE runner_registry_v5 SET status='online',heartbeat_at=?,updated_at=? WHERE id=?", (now, now, RUNNER_ID))
        _sync_runner_jobs(db, RUNNER_ID)
        if db.execute('SELECT active_jobs FROM runner_registry_v5 WHERE id=?', (RUNNER_ID,)).fetchone()[0] >= 32:
            return None
        for task in db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt<max_attempts "
                               "ORDER BY priority DESC,created_at,id", (group_id,)).fetchall():
            if (not w._load(task['context_capsule_json'], {}).get('team_plan')
                    or task['role'] not in {'researcher', 'explorer', 'specialist'} or not _group_can_lease(db, task)):
                continue
            try:
                _inputs(db, task)
            except HTTPException:
                db.execute("UPDATE agent_tasks SET status='paused',error_json=?,updated_at=? WHERE id=?",
                           (w._dump({'code': 'research_input_stale'}), now, task['id']))
                continue
            expires = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
            db.execute("UPDATE agent_tasks SET status='running',attempt=attempt+1,lease_owner=?,lease_expires_at=?,"
                       "heartbeat_at=?,error_json=NULL,updated_at=? WHERE id=? AND status='queued'", (RUNNER_ID, expires, now, now, task['id']))
            _sync_runner_jobs(db, RUNNER_ID)
            r._emit(db, task['id'], 'team_task.started', {'runner_id': RUNNER_ID}, task['campaign_id'])
            return db.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
    return None


def _complete(task, call, decision, output, input_tokens, output_tokens, runtime_ms):
    output = ResearchOutput.model_validate(output)
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _owned_running(db, task['id'], RUNNER_ID)
        _check_lease_attempt(current, task['attempt'])
        rows = _inputs(db, current)
        support = {row['id'] for row in rows if row['node_type'] in {'observation', 'evidence'}}
        counters = {row['id'] for row in rows if row['node_type'] == 'counterevidence'}
        if input_tokens + output_tokens > call['reserved_tokens'] or runtime_ms > call['max_runtime_ms']:
            raise HTTPException(409, 'research result exceeds call budget')
        for hypothesis in output.hypotheses:
            w._unique(hypothesis.evidence_ids, 'research evidence ids')
            w._unique(hypothesis.counterevidence_ids, 'research counterevidence ids')
            if not set(hypothesis.evidence_ids).issubset(support) or set(hypothesis.counterevidence_ids) != counters:
                raise HTTPException(409, 'research output invented evidence or omitted counterevidence')
            if any(not item.strip() or len(item) > 1000 for item in hypothesis.limitations):
                raise HTTPException(422, 'research limitations must be bounded nonempty strings')
        if any(not item.strip() or len(item) > 1000 for item in output.open_questions):
            raise HTTPException(422, 'research questions must be bounded nonempty strings')
        attrs = {'task_id': task['id'], 'group_id': task['group_id'], 'role': task['role'],
                 'decision_id': decision['id'], 'call_id': call['id'], 'canonical_result_eligible': False,
                 'research_input_hashes': w._load(current['context_capsule_json'], {})['research_input_hashes'],
                 'scope_snapshot_id': w._load(current['context_capsule_json'], {}).get('scope_snapshot_id'),
                 'policy_id': w._load(current['context_capsule_json'], {}).get('policy_id')}
        claims, questions = [], []
        for index, hypothesis in enumerate(output.hypotheses):
            node_id, _ = _bridge_node(db, task['campaign_id'], source_type='team_research',
                source_ref=f"{task['id']}:claim:{index}", node_type='claim', title=hypothesis.statement,
                body=hypothesis.statement, status='draft', run_id=task['run_id'],
                attributes=attrs | hypothesis.model_dump(mode='json'))
            claims.append(node_id)
            for source in hypothesis.evidence_ids:
                _bridge_edge(db, task['campaign_id'], source, node_id, 'supports')
            for source in hypothesis.counterevidence_ids:
                _bridge_edge(db, task['campaign_id'], source, node_id, 'contradicts')
        for index, question in enumerate(output.open_questions):
            node_id, _ = _bridge_node(db, task['campaign_id'], source_type='team_research',
                source_ref=f"{task['id']}:question:{index}", node_type='open_question', title=question,
                body=question, status='open', run_id=task['run_id'], attributes=attrs | {'not_evidence': True})
            questions.append(node_id)
        result = {'summary': output.summary, 'claim_ids': claims, 'open_question_ids': questions,
                  'decision_id': decision['id'], 'call_id': call['id'], 'status': 'research_draft'}
        # Includes known rejected/retried calls, rather than only the last successful attempt.
        usage = db.execute('SELECT COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'
                           'COALESCE(SUM(cost_micros),0) FROM runtime_usage WHERE task_id=?', (task['id'],)).fetchone()
        previous_usage = db.execute('SELECT runtime_ms FROM agent_task_usage WHERE task_id=?', (task['id'],)).fetchone()
        db.execute('INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
                   'input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,cost_micros=excluded.cost_micros,'
                   'runtime_ms=excluded.runtime_ms,updated_at=excluded.updated_at',
                   (task['id'], *usage, previous_usage['runtime_ms'] if previous_usage else runtime_ms, w._now()))
        db.execute("UPDATE agent_tasks SET status='succeeded',result_json=?,lease_owner=NULL,lease_expires_at=NULL,"
                   "heartbeat_at=NULL,updated_at=? WHERE id=?", (w._dump(result), w._now(), task['id']))
        _sync_runner_jobs(db, RUNNER_ID)
        r._emit(db, task['id'], 'team_task.draft_completed', {'claim_ids': claims, 'open_question_ids': questions}, task['campaign_id'])
        return result


def _execute(task):
    call = None
    try:
        with w._core().connect() as db:
            rows = _inputs(db, task)
            nodes = [{key: row[key] for key in ('id', 'node_type', 'title', 'body', 'status')} for row in rows]
        w._local_model_context(task, nodes)
        decision = _route(task)
        provider = _provider(decision)
        call = reserve_call(decision['id'], task['id'], RUNNER_ID, task['attempt'])
        from v5_model_inputs import prepare_call
        measured_start = time.monotonic()
        executing = prepare_call(provider, task, nodes, call)
        output, incoming, outgoing, runtime_ms = w._local_model_output(provider, executing, nodes)
        runtime_ms = max(runtime_ms, int((time.monotonic() - measured_start) * 1000))
        r.record_usage(r.UsageReport(decision_id=decision['id'], call_id=call['id'],
            idempotency_key=f"research:{task['id']}:{task['attempt']}", input_tokens=incoming, output_tokens=outgoing,
            cost_micros=r._estimated_cost(provider, incoming+outgoing) if decision['route']=='cloud' else 0,
            runtime_ms=runtime_ms))
        with w._core().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT SUM(input_tokens),SUM(output_tokens),SUM(cost_micros) FROM runtime_usage WHERE task_id=?', (task['id'],)).fetchone()
            from v5_runtime_calls import spent_runtime_ms
            total_runtime = spent_runtime_ms(db, task['id'])
            db.execute('INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
                       'input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,cost_micros=excluded.cost_micros,'
                       'runtime_ms=excluded.runtime_ms,updated_at=excluded.updated_at',
                       (task['id'], *used, total_runtime, w._now()))
        if incoming > executing['model_preflight']['input_tokens'] or outgoing > executing['model_preflight']['output_max_tokens']:
            raise HTTPException(409, 'model input or output token budget exceeded')
        return _complete(task, call, decision, output, incoming, outgoing, runtime_ms)
    except Exception as error:
        from v5_model_response import ModelOutputRejected, settle_rejected_output
        settlement_error = None
        if call and isinstance(error, ModelOutputRejected):
            try:
                settle_rejected_output(task, call, decision, provider, error,
                    int((time.monotonic() - measured_start) * 1000), 'research')
            except Exception as failure:
                settlement_error = type(failure).__name__
        if call:
            fail_call(call['id'])
        with w._core().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
            if (current and current['attempt'] == task['attempt']
                    and ((current['status'] == 'running' and current['lease_owner'] == RUNNER_ID)
                         or current['status'] == 'paused')):
                unknown = bool(call and db.execute("SELECT 1 FROM runtime_calls WHERE id=? AND state='unknown'", (call['id'],)).fetchone())
                code = ('model_usage_unknown' if unknown else 'model_output_invalid_usage_settled'
                        if isinstance(error, ModelOutputRejected) else 'research_context_too_large'
                        if isinstance(error, ValueError) and 'context exceeds 32 KB' in str(error)
                        else 'model_budget_blocked' if isinstance(error, HTTPException) and 'budget' in str(error.detail)
                        else 'research_worker_rejected')
                # Explicit restart/review required; no automatic model retry on rejected output.
                from v5_model_transport import ModelTransportError
                diagnostic = {'transport_code': error.code} if isinstance(error, ModelTransportError) else {}
                if settlement_error:
                    diagnostic['settlement_error_type'] = settlement_error
                db.execute("UPDATE agent_tasks SET status='paused',lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                           "error_json=?,updated_at=? WHERE id=?", (w._dump({'code': code, 'error_type': type(error).__name__, **diagnostic}), w._now(), task['id']))
                _sync_runner_jobs(db, RUNNER_ID)
        return {'status': 'paused', 'error_type': type(error).__name__}


def _run_group(group_id, concurrency):
    marker = f'research-execution:{group_id}'
    error_type = None
    try:
        def consume():
            while True:
                if _stopping.is_set():
                    return
                task = _claim(group_id)
                if not task:
                    return
                _execute(task)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            list(executor.map(lambda _: consume(), range(concurrency)))
    except Exception as error:
        error_type = type(error).__name__
    finally:
        with w._core().connect() as db:
            row = db.execute('SELECT value FROM app_metadata WHERE key=?', (marker,)).fetchone()
            value = w._load(row['value'], {}) if row else {}
            if value.get('runner_id') == RUNNER_ID:
                value['state'] = 'stopped'
                value['error_type'] = error_type
                db.execute('UPDATE app_metadata SET value=?,updated_at=? WHERE key=?', (w._dump(value), w._now(), marker))


@router.post('/groups/{group_id}/start', status_code=202)
def start_group_research(group_id: str):
    recover_expired_leases()
    with _lock:
        if _stopping.is_set():
            if any(job.is_alive() for job in _jobs.values()):
                raise HTTPException(409, 'research worker is stopping')
            _stopping.clear()
        if group_id in _jobs and _jobs[group_id].is_alive():
            return {'status': 'already_running', 'group_id': group_id}
        with w._core().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            recover_calls(db)
            group = db.execute('SELECT * FROM research_groups WHERE id=?', (group_id,)).fetchone()
            if not group or group['status'] != 'active' or group['strategy'] != 'bounded_research':
                raise HTTPException(409, 'active configured research team required')
            task = db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt<max_attempts ORDER BY created_at,id LIMIT 1", (group_id,)).fetchone()
            if not task:
                raise HTTPException(409, 'no queued research tasks; review paused tasks before restarting')
            # Legacy queue-only teams freeze the current graph on their first explicit start.
            if 'research_input_hashes' not in w._load(task['context_capsule_json'], {}):
                rows = db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND node_type IN ('observation','evidence','counterevidence','claim') "
                                  "AND status NOT IN ('archived','retired','invalidated') AND source_type!='team_research' "
                                  "ORDER BY (node_type='counterevidence') DESC,created_at DESC,id LIMIT 20", (group['campaign_id'],)).fetchall()
                hashes = {row['id']: w._node_digest(row) for row in rows}
                for queued in db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued'", (group_id,)).fetchall():
                    capsule = w._load(queued['context_capsule_json'], {}) | {'research_input_hashes': hashes}
                    db.execute('UPDATE agent_tasks SET context_capsule_json=? WHERE id=?', (w._dump(capsule), queued['id']))
                task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
            _inputs(db, task)
            profile = db.execute('SELECT * FROM runtime_profiles WHERE id=?', (group['runtime_profile_id'],)).fetchone()
            config = r.ProfileConfig.model_validate(w._load(profile['config_json'], {})) if profile else r.ProfileConfig()
            concurrency = min(w._load(group['budget_json'], {}).get('max_concurrency', 1), config.max_concurrent_calls, 32)
        # A route inspection is persisted, but cannot send a model request.
        _provider(_route(task))
        with w._core().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            marker = f'research-execution:{group_id}'
            prior = db.execute('SELECT value,updated_at FROM app_metadata WHERE key=?', (marker,)).fetchone()
            if prior and w._load(prior['value'], {}).get('state') == 'running' and prior['updated_at'] > (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat():
                raise HTTPException(409, 'research executor is active; wait for lease recovery after interruption')
            value = {'state': 'running', 'runner_id': RUNNER_ID}
            db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (marker, w._dump(value), w._now()))
            r._emit(db, group_id, 'team_research.start_requested', {'concurrency': concurrency}, group['campaign_id'])
        job = threading.Thread(target=_run_group, args=(group_id, concurrency), daemon=True, name=f'fieldwork-team-{group_id}')
        _jobs[group_id] = job
        job.start()
        return {'status': 'started', 'group_id': group_id, 'max_concurrency': concurrency, 'execution': 'profile_recorded_graph_research'}


def stop_workers():
    """Shutdown before app/test database bindings disappear; stop active model transports."""
    _stopping.set()
    with _lock:
        jobs = [job for job in _jobs.values() if job.is_alive()]
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        now = w._now()
        db.execute("UPDATE runtime_calls SET state='unknown',updated_at=? WHERE runner_id=? AND state='calling'", (now, RUNNER_ID))
        db.execute("UPDATE runtime_calls SET state='released',updated_at=? WHERE runner_id=? AND state='reserved'", (now, RUNNER_ID))
        db.execute("UPDATE agent_tasks SET status='paused',lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                   "error_json=?,updated_at=? WHERE lease_owner=? AND status='running'",
                   (w._dump({'code': 'research_worker_stopped'}), now, RUNNER_ID))
        _sync_runner_jobs(db, RUNNER_ID)
        db.execute("UPDATE runner_registry_v5 SET status='offline',updated_at=? WHERE id=?", (now, RUNNER_ID))
    for job in jobs:
        job.join(timeout=2)
