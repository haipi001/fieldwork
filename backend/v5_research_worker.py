"""Explicit, bounded team analysis of recorded graph inputs; outputs remain research drafts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone
import ipaddress
import threading
import time
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, ValidationError

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


ADMISSION_KEY = 'research_executor_admission'


def _admission(db):
    row = db.execute('SELECT value,updated_at FROM app_metadata WHERE key=?', (ADMISSION_KEY,)).fetchone()
    value = w._load(row['value'], {}) if row else {}
    state = value.get('state', 'accepting' if row is None else 'draining')
    return {'state': 'accepting' if state == 'accepting' else 'draining',
            'updated_at': row['updated_at'] if row else ''}


class AdmissionInput(w.StrictModel):
    state: Literal['accepting', 'draining']
    expected_updated_at: str = Field(max_length=100)


@router.get('/admission')
def research_admission():
    with _lock:
        groups = sum(job.is_alive() for job in _jobs.values())
    with w._core().connect() as db:
        value = _admission(db)
        active = db.execute("SELECT COUNT(*) FROM agent_tasks WHERE lease_owner=? AND status IN ('leased','running')", (RUNNER_ID,)).fetchone()[0]
    return {**value, 'runner_id': RUNNER_ID, 'active_groups': groups, 'active_tasks': active,
            'process_stopping': _stopping.is_set(), 'scope': 'builtin_research_worker',
            'automatic_start_on_resume': False}


@router.patch('/admission')
def set_research_admission(body: AdmissionInput):
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _admission(db)
        if body.expected_updated_at != current['updated_at']:
            raise HTTPException(409, 'Research executor state changed; refresh first')
        now = w._now()
        db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                   (ADMISSION_KEY, w._dump({'state':body.state}), now))
        r._emit(db, RUNNER_ID, 'research_executor.admission_changed', {'state':body.state})
    return research_admission()


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


def research_output_schema(nodes):
    """Bind the model contract to this call's evidence, without trusting compliance."""
    schema=ResearchOutput.model_json_schema()
    support=sorted({row['id'] for row in nodes if row['node_type'] in {'observation','evidence'}})
    counters=sorted({row['id'] for row in nodes if row['node_type']=='counterevidence'})
    fields=schema['$defs']['Hypothesis']['properties']
    fields['evidence_ids'].update(items={'type':'string','enum':support} if support else False, uniqueItems=True)
    fields['counterevidence_ids'].update(const=counters, uniqueItems=True,
        description='Copy this exact list of graph node IDs; do not replace it with observation IDs or omit any ID.')
    if not support:schema['properties']['hypotheses']['maxItems']=0
    return schema


RESEARCH_CONTRACT_REASONS={
    'research evidence ids must be unique':'duplicate_support_ids',
    'research counterevidence ids must be unique':'duplicate_counterevidence_ids',
    'research output invented evidence':'unapproved_support_ids',
    'research output omitted or invented counterevidence':'counterevidence_set_mismatch',
    'research limitations must be bounded nonempty strings':'invalid_limitation',
    'research questions must be bounded nonempty strings':'invalid_open_question',
    'research result exceeds call budget':'call_budget_exceeded',
}


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
        if not row or row['run_id'] not in (None,task['run_id']) or w._node_digest(row) != digest:
            raise HTTPException(409, 'research input graph changed')
        rows.append(row)
    # Registered browser facts must still match their source row before paid dispatch.
    ownership=[]
    for row in rows:
        attrs=w._load(row['attributes_json'],{})
        if not isinstance(attrs,dict):raise HTTPException(409,'research input graph changed')
        registered=attrs.get('ownership_projection') is True or attrs.get('evidence_type')=='http.ownership_denial'
        if row['source_type']=='artifact':
            artifact=db.execute('SELECT kind FROM artifacts WHERE id=? AND run_id=?',(row['source_ref'],task['run_id'])).fetchone()
            registered=registered or bool(artifact and artifact['kind']=='http.ownership_projection')
        if registered:ownership.append(row)
    if ownership:
        from campaign_materials import access
        from research_material_review import source_status
        with access():
            budget={'remaining':8000000,'checked':{}}
            if any(source_status(db,row,task['run_id'],budget)!='verified' for row in ownership):
                raise HTTPException(409,'research input graph changed')
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


def _execution_owner(db, group_id):
    row=db.execute('SELECT value FROM app_metadata WHERE key=?',(f'research-execution:{group_id}',)).fetchone()
    value=w._load(row['value'],{}) if row else None
    return value


def _heartbeat_execution(group_id):
    """A live dispatcher refreshes its marker; a replaced owner cannot claim again."""
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        value=_execution_owner(db,group_id)
        if not value or value.get('state')!='running' or value.get('runner_id')!=RUNNER_ID:return False
        db.execute('UPDATE app_metadata SET updated_at=? WHERE key=?',(w._now(),f'research-execution:{group_id}'))
        return True


@router.get('/groups/{group_id}/execution')
def group_execution(group_id:str):
    """Observe durable markers and leases without recovery or model execution."""
    with w._core().connect() as db:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM research_groups WHERE id=?',(group_id,)).fetchone():raise HTTPException(404,'Research group not found')
        row=db.execute('SELECT value,updated_at FROM app_metadata WHERE key=?',(f'research-execution:{group_id}',)).fetchone()
        marker=w._load(row['value'],{}) if row else {}
        counts={item['status']:item['n'] for item in db.execute('SELECT status,COUNT(*) AS n FROM agent_tasks WHERE group_id=? GROUP BY status',(group_id,))}
        calls={item['state']:item['n'] for item in db.execute('SELECT state,COUNT(*) AS n FROM runtime_calls WHERE group_id=? GROUP BY state',(group_id,))}
        leases=db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=? AND status IN ('leased','running') AND lease_owner IS NOT NULL AND lease_expires_at>?",(group_id,w._now())).fetchone()[0]
        interrupted=sum(w._load(item['error_json'],{}).get('code')=='model_call_interrupted_review' for item in db.execute("SELECT error_json FROM agent_tasks WHERE group_id=? AND status='paused'",(group_id,)))
        fresh=bool(row and row['updated_at']>(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat())
        state='not_started' if not row else 'active_marker' if marker.get('state')=='running' and fresh else 'stale_marker' if marker.get('state')=='running' else 'stopped'
        return {'group_id':group_id,'state':state,'last_heartbeat_at':row['updated_at'] if row else None,
            'interrupted_model_tasks':interrupted,'marker_runner_id':marker.get('runner_id'),'unexpired_leases':leases,'task_counts':counts,'call_counts':calls,
            'automatic_restart':False,'sends_model_requests':False,'marker_is_execution_proof':False,
            'unknown_usage_requires_review':bool(calls.get('unknown') or calls.get('calling'))}


def _claim(group_id):
    if _stopping.is_set():
        return None
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if _admission(db)['state'] != 'accepting':
            return None
        owner=_execution_owner(db,group_id)
        if owner and (owner.get('state')!='running' or owner.get('runner_id')!=RUNNER_ID):
            return None
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
            if owner and owner.get('resume_revision'):
                import research_resume
                policy=research_resume.read(db,group_id)
                if not policy or task['id'] not in policy['task_hashes']:continue
                try:research_resume.validate(db,group_id,owner['resume_revision'],task)
                except HTTPException:return None
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
            if not set(hypothesis.evidence_ids).issubset(support):
                raise HTTPException(409, 'research output invented evidence')
            if set(hypothesis.counterevidence_ids) != counters:
                raise HTTPException(409, 'research output omitted or invented counterevidence')
            if any(not item.strip() or len(item) > 1000 for item in hypothesis.limitations):
                raise HTTPException(422, 'research limitations must be bounded nonempty strings')
        if any(not item.strip() or len(item) > 1000 for item in output.open_questions):
            raise HTTPException(422, 'research questions must be bounded nonempty strings')
        attrs = {'task_id': task['id'], 'group_id': task['group_id'], 'role': task['role'],
                 'decision_id': decision['id'], 'call_id': call['id'], 'canonical_result_eligible': False,
                 'producer_task_id': task['id'], 'producer_runner_ref': RUNNER_ID,
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
        claim_hashes={identifier:w._node_digest(db.execute('SELECT * FROM research_nodes WHERE id=?',(identifier,)).fetchone()) for identifier in claims}
        result = {'summary': output.summary, 'claim_ids': claims, 'open_question_ids': questions,
                  'decision_id': decision['id'], 'call_id': call['id'], 'status': 'research_draft','claim_hashes':claim_hashes}
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
                         or (current['status'] == 'paused' and w._load(current['error_json'],{}).get('code')!='model_call_interrupted_review'))):
                unknown = bool(call and db.execute("SELECT 1 FROM runtime_calls WHERE id=? AND state='unknown'", (call['id'],)).fetchone())
                code = ('model_usage_unknown' if unknown else 'model_output_invalid_usage_settled'
                        if isinstance(error, ModelOutputRejected) else 'research_context_too_large'
                        if isinstance(error, ValueError) and 'context exceeds 32 KB' in str(error)
                        else 'model_budget_blocked' if isinstance(error, HTTPException) and 'budget' in str(error.detail)
                        else 'research_worker_rejected')
                # Explicit restart/review required; no automatic model retry on rejected output.
                from v5_model_transport import ModelTransportError
                diagnostic = {'transport_code': error.code} if isinstance(error, ModelTransportError) else {}
                if isinstance(error, HTTPException) and isinstance(error.detail,str) and error.detail in RESEARCH_CONTRACT_REASONS:
                    diagnostic['model_contract_reason']=RESEARCH_CONTRACT_REASONS[error.detail]
                if isinstance(error, ModelOutputRejected):
                    diagnostic.update(model_output_reason=error.reason_code, model_finish_reason=error.finish_reason, model_output_diagnostics=error.diagnostics)
                elif isinstance(error, ValidationError) and call and db.execute("SELECT 1 FROM runtime_calls WHERE id=? AND state='settled'", (call['id'],)).fetchone():
                    code = 'model_output_invalid_usage_settled'
                    diagnostic['model_output_reason'] = 'schema_mismatch'
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
        # Claims use the persistent concurrency gate. Poll while work is in flight so
        # a changed limit or newly added member takes effect without restarting.
        pending = set()
        with ThreadPoolExecutor(max_workers=32) as executor:
            while True:
                if not _stopping.is_set() and _heartbeat_execution(group_id):
                    with w._core().connect() as db:
                        row = db.execute('SELECT budget_json,status FROM research_groups WHERE id=?', (group_id,)).fetchone()
                    limit = min(32, max(1, w._load(row['budget_json'], {}).get('max_concurrency', concurrency))) if row and row['status']=='active' else 0
                    while len(pending) < limit:
                        task = _claim(group_id)
                        if not task:
                            break
                        pending.add(executor.submit(_execute, task))
                if not pending:
                    break
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
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
    return _start_group_research(group_id)


def _start_group_research(group_id: str, resume_revision=None):
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
            if _admission(db)['state'] != 'accepting':
                raise HTTPException(409, 'Research executor is not accepting new work')
            recover_calls(db)
            group = db.execute('SELECT * FROM research_groups WHERE id=?', (group_id,)).fetchone()
            if not group or group['status'] != 'active' or group['strategy'] != 'bounded_research':
                raise HTTPException(409, 'active configured research team required')
            if resume_revision:
                import research_resume
                approved=research_resume.validate(db,group_id,resume_revision)
                task=db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt=0 AND id IN (SELECT value FROM json_each(?)) ORDER BY created_at,id LIMIT 1",(group_id,w._dump(list(approved['task_hashes'])))).fetchone()
                if task:research_resume.validate(db,group_id,resume_revision,task)
            else:
                task = db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt<max_attempts ORDER BY created_at,id LIMIT 1", (group_id,)).fetchone()
            if not task:
                raise HTTPException(409, 'no queued research tasks; review paused tasks before restarting')
            # Legacy queue-only teams freeze the current graph on their first explicit start.
            if 'research_input_hashes' not in w._load(task['context_capsule_json'], {}):
                counter_count = db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=? AND (run_id IS NULL OR run_id=?) AND node_type='counterevidence' "
                                           "AND status NOT IN ('archived','retired','invalidated') AND source_type!='team_research'",(group['campaign_id'],task['run_id'])).fetchone()[0]
                if counter_count>20:raise HTTPException(409,'Run counterevidence exceeds the current research input limit')
                rows = db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND (run_id IS NULL OR run_id=?) AND node_type IN ('observation','evidence','counterevidence','claim') "
                                  "AND status NOT IN ('archived','retired','invalidated') AND source_type!='team_research' "
                                  "ORDER BY (node_type='counterevidence') DESC,created_at DESC,id LIMIT 20", (group['campaign_id'],task['run_id'])).fetchall()
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
            live_leases=db.execute("SELECT 1 FROM agent_tasks WHERE group_id=? AND status IN ('leased','running') AND lease_owner IS NOT NULL AND lease_expires_at>? LIMIT 1",(group_id,w._now())).fetchone()
            if live_leases or (prior and w._load(prior['value'], {}).get('state') == 'running' and prior['updated_at'] > (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()):
                raise HTTPException(409, 'research executor is active; wait for lease recovery after interruption')
            if resume_revision:
                research_resume.validate(db,group_id,resume_revision,task)
            value = {'state': 'running', 'runner_id': RUNNER_ID}
            if resume_revision:value['resume_revision']=resume_revision
            db.execute('INSERT INTO app_metadata VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (marker, w._dump(value), w._now()))
            r._emit(db, group_id, 'team_research.start_requested', {'concurrency': concurrency}, group['campaign_id'])
        from storage_access import storage_call
        job = threading.Thread(target=storage_call, args=(w._core().LOCAL_DATA_ROOT.resolve(), _run_group, group_id, concurrency), daemon=True, name=f'fieldwork-team-{group_id}')
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


@router.get('/tasks/{task_id}/draft')
def read_research_draft(task_id: str):
    """Read saved draft lineage; never rerun a model or attest verification."""
    from v5_graph import _node
    with w._core().connect() as db:
        db.execute('BEGIN')
        task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, 'research task not found')
        result = w._load(task['result_json'], {})
        if not isinstance(result, dict) or result.get('status') != 'research_draft':
            raise HTTPException(409, 'task has no saved research draft')
        capsule = w._load(task['context_capsule_json'], {})
        hashes = capsule.get('research_input_hashes', {}) if isinstance(capsule, dict) else None
        if not isinstance(hashes, dict) or len(hashes) > 20 or any(not isinstance(k, str) or not isinstance(v, str) for k,v in hashes.items()):
            raise HTTPException(409, 'saved research input references are invalid')
        drafts = []
        for field, kind, limit, prefix in [('claim_ids','claim',5,'claim'),('open_question_ids','open_question',10,'question')]:
            ids = result.get(field, [])
            if not isinstance(ids, list) or len(ids) > limit or any(not isinstance(value,str) for value in ids) or len(set(ids)) != len(ids):
                raise HTTPException(409, 'saved research draft references are invalid')
            for index, node_id in enumerate(ids):
                row = db.execute('SELECT * FROM research_nodes WHERE id=?', (node_id,)).fetchone()
                if (not row or row['campaign_id'] != task['campaign_id'] or row['run_id'] != task['run_id']
                        or row['node_type'] != kind or row['source_type'] != 'team_research'
                        or row['source_ref'] != f'{task_id}:{prefix}:{index}'):
                    raise HTTPException(409, 'saved research draft provenance is missing or changed')
                attrs = w._load(row['attributes_json'], {})
                if not isinstance(attrs,dict) or attrs.get('task_id') != task_id:
                    raise HTTPException(409, 'saved research draft provenance is invalid')
                drafts.append(_node(row))
        from research_material_review import source_status
        from storage_access import storage_access
        inputs = []
        source_budget = {'remaining':8_000_000,'checked':{}}
        for node_id, digest in hashes.items():
            row = db.execute('SELECT * FROM research_nodes WHERE id=?', (node_id,)).fetchone()
            if not row or row['campaign_id'] != task['campaign_id'] or row['run_id'] not in (None,task['run_id']):
                inputs.append({'id':node_id,'material_status':'missing','source_status':'missing'})
            else:
                current = w._node_digest(row) == digest and row['status'] not in {'archived','retired','invalidated'}
                try:
                    with storage_access(w._core().LOCAL_DATA_ROOT):
                        source = source_status(db, row, task['run_id'], source_budget)
                except (OSError, ValueError, RuntimeError):
                    source = 'unavailable'
                inputs.append({**_node(row),'material_status':'current' if current else 'changed','source_status':source})
        return {'task_id':task_id,'campaign_id':task['campaign_id'],'run_id':task['run_id'],
                'summary':result.get('summary',''),'drafts':drafts,'inputs':inputs,
                'inputs_current':bool(inputs) and all(item['material_status']=='current' for item in inputs),
                'registered_sources_verified':bool(inputs) and all(item['source_status']=='verified' for item in inputs),
                'source_check_scope':'registered_relations_and_bounded_file_sha256',
                'independently_verified':False,'canonical_result_eligible':False}


class DraftCandidateInput(w.StrictModel):
    authorized: bool = False
    target: str = Field(min_length=1,max_length=2000)
    preview_hash: str = Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')


@router.get('/tasks/{task_id}/drafts/{claim_id}/candidate-plan')
def draft_candidate_plan(task_id:str,claim_id:str,target:str|None=Query(default=None,max_length=2000)):
    import research_candidate_handoff as handoff
    from campaign_materials import access
    with access(),w._core().connect() as db:
        return handoff.public_plan(handoff.plan(db,task_id,claim_id,target))


@router.post('/tasks/{task_id}/drafts/{claim_id}/candidate',status_code=201)
def create_draft_candidate(task_id:str,claim_id:str,body:DraftCandidateInput):
    if not body.authorized:raise HTTPException(422,'Review the candidate proposal and confirm first')
    import research_candidate_handoff as handoff
    return handoff.create(task_id,claim_id,body.target,body.preview_hash)


class BatchHandoffItem(w.StrictModel):
    task_id: str = Field(min_length=1,max_length=120)
    claim_id: str = Field(min_length=1,max_length=120)
    target: str | None = Field(default=None,min_length=1,max_length=2000)


class BatchHandoffPlan(w.StrictModel):
    items: list[BatchHandoffItem] = Field(min_length=1,max_length=3)


class BatchHandoffCreate(BatchHandoffPlan):
    authorized: bool = Field(default=False,strict=True)
    preview_hash: str = Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')


@router.get('/groups/{group_id}/candidate-handoff-options')
def group_handoff_options(group_id:str,offset:int=Query(default=0,ge=0,le=1000000)):
    import research_batch_handoff as batch
    return batch.options(group_id,offset)


@router.get('/groups/{group_id}/candidate-handoff-prepare')
def group_handoff_prepare(group_id:str,offset:int=Query(default=0,ge=0,le=1000000)):
    import research_batch_handoff as batch
    return batch.prepare(group_id,offset)


@router.post('/groups/{group_id}/candidate-handoff-plan')
def group_handoff_plan(group_id:str,body:BatchHandoffPlan):
    import research_batch_handoff as batch
    return batch.preview(group_id,[item.model_dump() for item in body.items])


@router.post('/groups/{group_id}/candidate-handoff',status_code=201)
def group_handoff_create(group_id:str,body:BatchHandoffCreate):
    if not body.authorized:raise HTTPException(422,'Review the complete batch and confirm first')
    import research_batch_handoff as batch
    return batch.create(group_id,[item.model_dump() for item in body.items],body.preview_hash)


class ResumeApproval(w.StrictModel):
    candidate_limit: int = Field(default=0,ge=0,le=3,strict=True)
    candidate_creation_confirmed: bool = Field(default=False,strict=True)
    confirmed: bool = Field(default=False,strict=True)
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ResumeDisable(w.StrictModel):
    revision: str = Field(pattern=r'^[a-f0-9]{32}$')


@router.get('/groups/{group_id}/resume-policy')
def read_resume_policy(group_id:str):
    import research_resume
    return research_resume.status(group_id)


@router.post('/groups/{group_id}/resume-plan')
@router.get('/groups/{group_id}/resume-plan')
def preview_resume_policy(group_id:str,candidate_limit:int=Query(default=0,ge=0,le=3)):
    import research_resume
    return research_resume.preview(group_id,candidate_limit)


@router.post('/groups/{group_id}/resume-policy')
def enable_resume_policy(group_id:str,body:ResumeApproval):
    if not body.confirmed:raise HTTPException(422,'Review exact unstarted tasks and confirm automatic model dispatch')
    import research_resume
    if body.candidate_limit and not body.candidate_creation_confirmed:raise HTTPException(422,'Confirm automatic unverified candidate creation separately')
    return research_resume.enable(group_id,body.preview_hash,body.candidate_limit)


@router.post('/groups/{group_id}/resume-policy/disable')
def disable_resume_policy(group_id:str,body:ResumeDisable):
    import research_resume
    return research_resume.disable(group_id,body.revision)
