"""Durable model-call reservations. Unknown consumption is never released automatically."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi import HTTPException


def _runtime():
    import v5_runtime
    return v5_runtime


def recover_calls(db: sqlite3.Connection) -> None:
    r = _runtime()
    now = r._now()
    # 'calling' is committed before dispatch. A lost response cannot prove zero consumption.
    db.execute("UPDATE runtime_calls SET state='unknown',updated_at=? WHERE state='calling' AND deadline_at<=?", (now, now))
    # A reserved call cannot dispatch without the guarded, transactional start transition.
    db.execute("UPDATE runtime_calls SET state='released',updated_at=? WHERE state='reserved' AND deadline_at<=?", (now, now))


def _spent(db: sqlite3.Connection, field: str, value, since: str | None = None) -> tuple[int, int]:
    assert field in {'task_id', 'campaign_id', 'group_id', 'profile_id'}
    clauses, params = [f'd.{field} IS ?' if field == 'profile_id' else f'u.{field}=?'], [value]
    if field == 'group_id':
        clauses[0] = 't.group_id=?'
    if since:
        clauses.append('u.created_at>=?')
        params.append(since)
    row = db.execute(
        'SELECT COALESCE(SUM(u.input_tokens+u.output_tokens),0),COALESCE(SUM(u.cost_micros),0) '
        'FROM runtime_usage u LEFT JOIN runtime_usage_reports rep ON rep.usage_id=u.id '
        'LEFT JOIN runtime_route_decisions d ON d.id=rep.decision_id '
        'LEFT JOIN agent_tasks t ON t.id=u.task_id WHERE ' + ' AND '.join(clauses), params).fetchone()
    return int(row[0]), int(row[1])


def _pending(db: sqlite3.Connection, field: str, value) -> tuple[int, int]:
    assert field in {'task_id', 'campaign_id', 'group_id', 'profile_id'}
    row = db.execute(f"SELECT COALESCE(SUM(reserved_tokens),0),COALESCE(SUM(reserved_cost_micros),0) "
                     f"FROM runtime_calls WHERE {field} IS ? AND state IN ('reserved','calling','unknown')", (value,)).fetchone()
    return int(row[0]), int(row[1])


def spent_runtime_ms(db: sqlite3.Connection, task_id: str) -> int:
    """Known calls use immutable duration; missing/unknown duration holds the full bound."""
    duration = db.execute("SELECT COALESCE(SUM(COALESCE(t.runtime_ms,c.max_runtime_ms)),0) "
                          "+ COALESCE(MAX(t.legacy_runtime_ms),0) "
                          "FROM runtime_calls c LEFT JOIN runtime_call_timings t ON t.call_id=c.id "
                          "WHERE c.task_id=? AND c.state!='released'", (task_id,)).fetchone()[0]
    legacy = db.execute('SELECT runtime_ms FROM agent_task_usage WHERE task_id=?', (task_id,)).fetchone()
    return max(int(duration), int(legacy[0]) if legacy else 0)


def reserve_call(decision_id: str, task_id: str, runner_id: str, attempt: int) -> dict:
    r = _runtime()
    from v5_orchestration import _owned_running, _check_lease_attempt
    with r._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        recover_calls(db)
        task = _owned_running(db, task_id, runner_id)
        _check_lease_attempt(task, attempt)
        if db.execute("SELECT 1 FROM runtime_calls WHERE task_id=? AND attempt=?", (task_id, attempt)).fetchone():
            raise HTTPException(409, 'model call attempt already reserved; dispatch cannot be replayed')
        if db.execute("SELECT 1 FROM runtime_calls WHERE task_id=? AND state IN ('reserved','calling','unknown')", (task_id,)).fetchone():
            raise HTTPException(409, 'task has an unresolved model call')
        decision = db.execute('SELECT * FROM runtime_route_decisions WHERE id=? AND task_id=?', (decision_id, task_id)).fetchone()
        if not decision or decision['status'] != 'selected':
            raise HTTPException(409, 'model call requires selected task route')
        provider = db.execute("SELECT * FROM runtime_providers WHERE id=? AND enabled=1 AND last_health='healthy'",
                              (decision['provider_id'],)).fetchone()
        snapshot = r._load(decision['request_json'], {}).get('provider_configuration_sha256')
        if not provider or not snapshot or r._provider_config_hash(provider) != snapshot:
            raise HTTPException(409, 'model provider configuration changed')
        profile = db.execute('SELECT * FROM runtime_profiles WHERE id=?', (decision['profile_id'],)).fetchone()
        config = r.ProfileConfig.model_validate(r._load(profile['config_json'], {})) if profile else r.ProfileConfig()
        active = db.execute("SELECT COUNT(*) FROM runtime_calls WHERE profile_id IS ? AND state IN ('reserved','calling')",
                            (decision['profile_id'],)).fetchone()[0]
        if active >= config.max_concurrent_calls:
            raise HTTPException(409, 'profile model concurrency budget exhausted')
        task_budget = r._load(task['budget_json'], {})
        spent_tokens, spent_cost = _spent(db, 'task_id', task_id)
        task_usage = db.execute('SELECT * FROM agent_task_usage WHERE task_id=?', (task_id,)).fetchone()
        if task_usage:
            spent_tokens = max(spent_tokens, task_usage['input_tokens'] + task_usage['output_tokens'])
            spent_cost = max(spent_cost, task_usage['cost_micros'])
        tokens = min(decision['max_tokens'], int(task_budget.get('max_tokens', decision['max_tokens'])) - spent_tokens)
        now_dt = datetime.now(timezone.utc)
        hourly = _spent(db, 'profile_id', decision['profile_id'], (now_dt - timedelta(hours=1)).isoformat())[0]
        held = _pending(db, 'profile_id', decision['profile_id'])[0]
        tokens = min(tokens, config.max_tokens_per_hour - hourly - held)
        if tokens <= 0:
            raise HTTPException(409, 'model token budget exhausted')
        cost = r._estimated_cost(provider, tokens) if r._location(provider) == 'cloud' else 0
        if cost > decision['max_cost_micros'] or cost + spent_cost > task_budget.get('max_cost_micros', cost + spent_cost):
            raise HTTPException(409, 'task model cost budget exhausted')
        campaign_spent = _spent(db, 'campaign_id', task['campaign_id'])[1]
        campaign_held = _pending(db, 'campaign_id', task['campaign_id'])[1]
        if config.max_cost_micros and campaign_spent + campaign_held + cost > config.max_cost_micros:
            raise HTTPException(409, 'campaign model cost budget exhausted')
        if task['group_id']:
            group = db.execute('SELECT * FROM research_groups WHERE id=?', (task['group_id'],)).fetchone()
            if not group or group['status'] != 'active':
                raise HTTPException(409, 'research group is not active')
            budget = r._load(group['budget_json'], {})
            count = db.execute("SELECT COUNT(*) FROM runtime_calls WHERE group_id=? AND state IN ('reserved','calling')", (task['group_id'],)).fetchone()[0]
            if count >= budget.get('max_concurrency', 1):
                raise HTTPException(409, 'group model concurrency budget exhausted')
            if (_spent(db, 'group_id', task['group_id'])[1] + _pending(db, 'group_id', task['group_id'])[1]
                    + cost > budget.get('max_cost_micros', 0)):
                raise HTTPException(409, 'group model cost budget exhausted')
        capsule = r._load(task['context_capsule_json'], {})
        if capsule.get('continuous_research'):
            policy_row = db.execute('SELECT policy_json FROM continuous_research_state WHERE campaign_id=?', (task['campaign_id'],)).fetchone()
            policy = r._load(policy_row['policy_json'], {}) if policy_row else {}
            cap = policy.get('daily_budget_micros', 0)
            if cap and _spent(db, 'campaign_id', task['campaign_id'], now_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat())[1] + campaign_held + cost > cap:
                raise HTTPException(409, 'continuous daily model budget exhausted')
        runtime_ms = min(config.max_runtime_ms_per_call,
                         int(task_budget.get('max_runtime_ms', 45_000)) - spent_runtime_ms(db, task_id))
        if task['lease_expires_at']:
            runtime_ms = min(runtime_ms, int((datetime.fromisoformat(task['lease_expires_at']) - now_dt).total_seconds() * 1000))
        if runtime_ms <= 0:
            raise HTTPException(409, 'model call runtime budget exhausted')
        call_id, now = r._uid('call'), now_dt.isoformat()
        deadline = (now_dt + timedelta(milliseconds=runtime_ms)).isoformat()
        db.execute('INSERT INTO runtime_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            call_id, decision_id, task_id, attempt, runner_id, task['campaign_id'], task['group_id'],
            decision['profile_id'], provider['id'], tokens, cost, runtime_ms, deadline, 'reserved', None, now, now))
        if capsule.get('team_plan'):
            from v6_model_gateway import bind_research_call
            bind_research_call(db, call_id=call_id, task=task, route=decision, runner_id=runner_id)
        r._emit(db, call_id, 'call.reserved', {'task_id': task_id, 'reserved_tokens': tokens}, task['campaign_id'])
        return dict(db.execute('SELECT * FROM runtime_calls WHERE id=?', (call_id,)).fetchone())


def start_call(call_id: str) -> None:
    r = _runtime()
    from v5_orchestration import _owned_running, _check_lease_attempt
    denial = None
    with r._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM runtime_calls WHERE id=?', (call_id,)).fetchone()
        if not row or row['state'] != 'reserved' or row['deadline_at'] <= r._now():
            raise HTTPException(409, 'model call cannot be started or replayed')
        task = _owned_running(db, row['task_id'], row['runner_id'])
        _check_lease_attempt(task, row['attempt'])
        decision = db.execute('SELECT * FROM runtime_route_decisions WHERE id=?', (row['decision_id'],)).fetchone()
        provider = db.execute("SELECT * FROM runtime_providers WHERE id=? AND enabled=1 AND last_health='healthy'", (row['provider_id'],)).fetchone()
        if not provider or r._provider_config_hash(provider) != r._load(decision['request_json'], {}).get('provider_configuration_sha256'):
            raise HTTPException(409, 'model provider changed before dispatch')
        capsule = r._load(task['context_capsule_json'], {})
        if capsule.get('team_plan'):
            from v6_model_gateway import authorize_research_start
            allowed, reason = authorize_research_start(db, row, task)
            if not allowed:
                denial = reason
                db.execute("UPDATE runtime_calls SET state='released',updated_at=? WHERE id=?", (r._now(), call_id))
                r._emit(db, call_id, 'call.released', {'task_id': row['task_id'], 'reason': reason}, row['campaign_id'])
        if denial is None:
            db.execute("UPDATE runtime_calls SET state='calling',updated_at=? WHERE id=?", (r._now(), call_id))
            r._emit(db, call_id, 'call.calling', {'task_id': row['task_id']}, row['campaign_id'])
    if denial is not None:
        raise HTTPException(409, f'model gateway denied dispatch: {denial}')


def fail_call(call_id: str) -> None:
    r = _runtime()
    with r._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM runtime_calls WHERE id=?', (call_id,)).fetchone()
        if row and row['state'] in {'reserved', 'calling'}:
            state = 'released' if row['state'] == 'reserved' else 'unknown'
            db.execute('UPDATE runtime_calls SET state=?,updated_at=? WHERE id=?', (state, r._now(), call_id))
            r._emit(db, call_id, f'call.{state}', {'task_id': row['task_id']}, row['campaign_id'])
