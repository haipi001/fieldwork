from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from fastapi import HTTPException

import final_core
import v5_runtime as runtime
from v5_runtime_calls import reserve_call, start_call, fail_call
from tests.test_final import client
from tests.test_v5_workers import setup_campaign, node


def setup(client, concurrency=1, hourly=100):
    _, cid = setup_campaign(client)
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'call-fixture', 'llama_cpp', 'Budget fixture', 'http://127.0.0.1:1', 'fixture', None,
            1, '{"location":"local"}', 'healthy', now, now, now))
    response = client.put('/api/v1/runtime/config', json={'name': 'Budget profile', 'mode': 'local',
        'config': {'local_provider_ids': ['call-fixture'], 'max_tokens_per_call': 60,
                   'max_tokens_per_hour': hourly, 'max_concurrent_calls': concurrency}})
    assert response.status_code == 201, response.text
    profile = response.json()['id']
    assert client.put('/api/v1/runners/call-runner', json={'id': 'call-runner', 'name': 'Call runner',
        'kind': 'worker', 'labels': {'location': 'local'}, 'capabilities': ['structured_critic'],
        'max_concurrency': 8}).status_code == 200
    return cid, profile


def task_route(client, cid, profile, key, tokens=80):
    claim = node(client, cid, 'claim', f'Bounded model call {key}')
    response = client.post('/api/v1/workers/tasks', json={'campaign_id': cid, 'role': 'critic',
        'claim_ids': [claim['id']], 'objective': 'Budget accounting fixture', 'idempotency_key': key,
        'runtime_profile_id': profile, 'max_tokens': tokens})
    assert response.status_code == 201, response.text
    task = client.post('/api/v1/orchestration/lease', json={'runner_id': 'call-runner', 'lease_seconds': 300}).json()['task']
    assert task['id'] == response.json()['id']
    decision = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=tokens))
    return task, decision


def test_concurrent_reservations_cannot_overbook_profile(client):
    cid, profile = setup(client, concurrency=1, hourly=200)
    pairs = [task_route(client, cid, profile, str(i)) for i in range(2)]
    def reserve(pair):
        task, decision = pair
        try:
            return reserve_call(decision['id'], task['id'], 'call-runner', task['attempt'])
        except HTTPException as error:
            return error.detail
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, pairs))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 'profile model concurrency budget exhausted' in results
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0] == 1


def test_settlement_unknown_hour_budget_and_retry_are_durable(client):
    cid, profile = setup(client, concurrency=4, hourly=100)
    first, route = task_route(client, cid, profile, 'known')
    call = reserve_call(route['id'], first['id'], 'call-runner', first['attempt'])
    assert call['reserved_tokens'] == 60
    start_call(call['id'])
    report = runtime.UsageReport(decision_id=route['id'], call_id=call['id'],
                                idempotency_key='settle-known', input_tokens=12, output_tokens=8)
    usage = runtime.record_usage(report)
    assert runtime.record_usage(report)['id'] == usage['id']
    with pytest.raises(HTTPException):
        start_call(call['id'])
    second, route2 = task_route(client, cid, profile, 'unknown')
    unknown = reserve_call(route2['id'], second['id'], 'call-runner', second['attempt'])
    start_call(unknown['id'])
    fail_call(unknown['id'])
    third, route3 = task_route(client, cid, profile, 'remaining')
    remainder = reserve_call(route3['id'], third['id'], 'call-runner', third['attempt'])
    assert remainder['reserved_tokens'] == 20
    start_call(remainder['id'])
    fail_call(remainder['id'])
    fourth, route4 = task_route(client, cid, profile, 'exhausted')
    with pytest.raises(HTTPException, match='token budget exhausted'):
        reserve_call(route4['id'], fourth['id'], 'call-runner', fourth['attempt'])
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET attempt=attempt+1 WHERE id=?', (second['id'],))
    with pytest.raises(HTTPException, match='unresolved model call'):
        reserve_call(route2['id'], second['id'], 'call-runner', second['attempt'] + 1)
    calls = client.get('/api/v1/runtime/calls').json()
    assert calls['states']['settled']['count'] == 1 and calls['states']['unknown']['count'] == 2
    with final_core.connect() as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable|invalid runtime call transition'):
            db.execute('UPDATE runtime_calls SET reserved_tokens=1 WHERE id=?', (unknown['id'],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE runtime_calls SET state='released' WHERE id=?", (unknown['id'],))


def test_expired_reserved_releases_but_dispatched_becomes_unknown(client, monkeypatch):
    cid, profile = setup(client, concurrency=2, hourly=200)
    pairs = [task_route(client, cid, profile, str(i)) for i in range(2)]
    calls = [reserve_call(route['id'], task['id'], 'call-runner', task['attempt']) for task, route in pairs]
    start_call(calls[1]['id'])
    monkeypatch.setattr(runtime, '_now', lambda: (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat())
    states = {row['id']: row['state'] for row in client.get('/api/v1/runtime/calls').json()['items']}
    assert states[calls[0]['id']] == 'released' and states[calls[1]['id']] == 'unknown'
    with pytest.raises(HTTPException):
        start_call(calls[0]['id'])


def test_task_token_cap_is_cumulative_across_attempts(client):
    cid, profile = setup(client, concurrency=2, hourly=1000)
    task, route = task_route(client, cid, profile, 'retry-budget', tokens=30)
    call = reserve_call(route['id'], task['id'], 'call-runner', task['attempt'])
    start_call(call['id'])
    runtime.record_usage(runtime.UsageReport(decision_id=route['id'], call_id=call['id'],
        idempotency_key='first-attempt-usage', input_tokens=10, output_tokens=15, runtime_ms=1))
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET attempt=2 WHERE id=?', (task['id'],))
    new_route = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=30))
    remaining = reserve_call(new_route['id'], task['id'], 'call-runner', 2)
    assert remaining['reserved_tokens'] == 5


def test_runtime_remaining_and_atomic_immutable_settlement(client):
    cid, profile = setup(client, concurrency=2, hourly=1000)
    task, route = task_route(client, cid, profile, 'runtime-retry', tokens=80)
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET budget_json=? WHERE id=?',
                   ('{"max_tokens":80,"max_runtime_ms":1000}', task['id']))
    call = reserve_call(route['id'], task['id'], 'call-runner', 1)
    start_call(call['id'])
    report = runtime.UsageReport(decision_id=route['id'], call_id=call['id'],
                                idempotency_key='runtime-first', input_tokens=1, output_tokens=1, runtime_ms=700)
    first = runtime.record_usage(report)
    assert runtime.record_usage(report)['id'] == first['id']
    with pytest.raises(HTTPException, match='different report'):
        runtime.record_usage(report.model_copy(update={'runtime_ms': 0}))
    with final_core.connect() as db:
        assert db.execute('SELECT runtime_ms FROM runtime_call_timings').fetchone()[0] == 700
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('UPDATE runtime_call_timings SET runtime_ms=0')
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM runtime_call_timings')
        db.execute('UPDATE agent_tasks SET attempt=2 WHERE id=?', (task['id'],))
    second_route = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=60))
    second = reserve_call(second_route['id'], task['id'], 'call-runner', 2)
    assert second['max_runtime_ms'] == 300
    start_call(second['id'])
    runtime.record_usage(runtime.UsageReport(decision_id=second_route['id'], call_id=second['id'],
        idempotency_key='runtime-second', input_tokens=1, output_tokens=1, runtime_ms=300))
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET attempt=3 WHERE id=?', (task['id'],))
    third_route = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=60))
    with pytest.raises(HTTPException, match='runtime budget exhausted'):
        reserve_call(third_route['id'], task['id'], 'call-runner', 3)


def test_missing_historical_duration_holds_full_runtime_bound(client):
    cid, profile = setup(client, hourly=1000)
    task, route = task_route(client, cid, profile, 'legacy-duration')
    call = reserve_call(route['id'], task['id'], 'call-runner', 1)
    start_call(call['id'])
    runtime.record_usage(runtime.UsageReport(decision_id=route['id'], call_id=call['id'],
        idempotency_key='legacy-no-duration', input_tokens=1))
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET attempt=2 WHERE id=?', (task['id'],))
    new_route = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=60))
    with pytest.raises(HTTPException, match='runtime budget exhausted'):
        reserve_call(new_route['id'], task['id'], 'call-runner', 2)


def test_legacy_task_duration_remains_in_cumulative_budget(client):
    cid, profile = setup(client, hourly=1000)
    task, route = task_route(client, cid, profile, 'legacy-runtime')
    with final_core.connect() as db:
        db.execute('UPDATE agent_tasks SET budget_json=? WHERE id=?',
                   ('{"max_tokens":80,"max_runtime_ms":1000}', task['id']))
        db.execute('INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?)', (task['id'], 0, 0, 0, 300, final_core.utcnow()))
    call = reserve_call(route['id'], task['id'], 'call-runner', 1)
    assert call['max_runtime_ms'] == 700
    start_call(call['id'])
    runtime.record_usage(runtime.UsageReport(decision_id=route['id'], call_id=call['id'],
        idempotency_key='legacy-runtime-plus-new', input_tokens=1, runtime_ms=200))
    with final_core.connect() as db:
        from v5_runtime_calls import spent_runtime_ms
        assert spent_runtime_ms(db, task['id']) == 500
        db.execute('UPDATE agent_tasks SET attempt=2 WHERE id=?', (task['id'],))
    reported = client.get(f'/api/v1/orchestration/tasks/{task["id"]}').json()
    assert reported['usage']['runtime_ms'] == 500
    assert reported['model_runtime']['charged_ms'] == 500
    new_route = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='critic', estimated_tokens=60))
    assert reserve_call(new_route['id'], task['id'], 'call-runner', 2)['max_runtime_ms'] == 500


def test_cloud_cost_reservations_cannot_overbook_campaign(client):
    cid, _ = setup(client, concurrency=4, hourly=1000)
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
            'cloud-cost-fixture', 'openai_compatible', 'No-network budget fixture',
            'https://model.example.test/v1', 'fixture', None, 1,
            '{"location":"cloud","cost_micros_per_million_tokens":1000000}', 'healthy', now, now, now))
    response = client.put('/api/v1/runtime/config', json={'name': 'Cloud cost reservation', 'mode': 'cloud',
        'config': {'cloud_provider_ids': ['cloud-cost-fixture'], 'max_cost_micros': 90,
                   'max_tokens_per_call': 60, 'max_concurrent_calls': 4}})
    assert response.status_code == 201, response.text
    profile = response.json()['id']
    pairs = []
    for index in range(2):
        response = client.post('/api/v1/orchestration/tasks', json={'campaign_id': cid,
            'role': 'researcher', 'objective': 'Public cost reservation fixture',
            'idempotency_key': f'cloud-cost-{index}', 'context_capsule': {'runtime_profile_id': profile},
            'budget': {'max_tokens': 60, 'max_cost_micros': 90}})
        assert response.status_code == 201, response.text
        task = client.post('/api/v1/orchestration/lease', json={'runner_id': 'call-runner', 'lease_seconds': 300}).json()['task']
        decision = runtime.route_task(runtime.RouteRequest(task_id=task['id'], task_type='public_research',
            sensitivity='public', estimated_tokens=60, budget_remaining_micros=90))
        assert decision['route'] == 'cloud' and decision['estimated_cost_micros'] == 60
        pairs.append((task, decision))
    def reserve(pair):
        task, decision = pair
        try:
            return reserve_call(decision['id'], task['id'], 'call-runner', task['attempt'])
        except HTTPException as error:
            return error.detail
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, pairs))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert 'campaign model cost budget exhausted' in results
    with final_core.connect() as db:
        assert db.execute('SELECT SUM(reserved_cost_micros) FROM runtime_calls').fetchone()[0] == 60


def test_usage_settlement_rolls_back_with_reconciliation_transaction(client):
    cid, profile = setup(client)
    task, decision = task_route(client, cid, profile, 'atomic-reconciliation')
    call = reserve_call(decision['id'], task['id'], 'call-runner', task['attempt'])
    start_call(call['id'])
    fail_call(call['id'])
    report = runtime.UsageReport(decision_id=decision['id'], call_id=call['id'],
                                idempotency_key='atomic-review', input_tokens=10, output_tokens=5)
    with final_core.connect() as db:
        with pytest.raises(RuntimeError, match='active transaction'):
            runtime.record_usage_in_transaction(db, report)
    with pytest.raises(RuntimeError, match='review persistence failed'):
        with final_core.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            runtime.record_usage_in_transaction(db, report)
            assert db.execute('SELECT state FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()[0] == 'settled'
            raise RuntimeError('review persistence failed')
    with final_core.connect() as db:
        assert db.execute('SELECT state,usage_id FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()['state'] == 'unknown'
        assert db.execute('SELECT COUNT(*) FROM runtime_usage_reports').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
        from v5_runtime_calls import _pending
        assert _pending(db, 'task_id', task['id'])[0] == call['reserved_tokens']
    settled = runtime.record_usage(report)
    assert runtime.record_usage(report)['id'] == settled['id']
