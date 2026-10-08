import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import final_core as core
from tests.test_final import client, create_ready
from tests.test_v5_runtime_calls import setup, task_route
from v5_runtime_calls import reserve_call, start_call, fail_call, _pending


def test_unknown_reconciliation_is_evidence_bound_atomic_and_does_not_resume(client, tmp_path):
    cid, profile = setup(client)
    task, decision = task_route(client, cid, profile, 'review-unknown')
    call = reserve_call(decision['id'], task['id'], 'call-runner', task['attempt'])
    start_call(call['id']); fail_call(call['id'])
    ready = create_ready(client, target='https://usage-review.example.test')
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('review-run', ready['id'], 'traditional', ready['current_scope_snapshot_id'], ready['current_policy_id'],
             'completed', 'report', 1, None, None, None, None, core.utcnow()))
        db.execute('UPDATE agent_tasks SET run_id=? WHERE id=?', ('review-run', task['id']))
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET status='paused' WHERE id=?", (task['id'],))
    material = dict(call_id=call['id'], decision_id=decision['id'], provider_id=call['provider_id'],
                    review_kind='operator_review', input_tokens=10, output_tokens=5, cost_micros=0, runtime_ms=50)
    path = tmp_path / 'review.json'
    def store(value):
        path.write_text(json.dumps(value))
        with core.connect() as db:
            db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                ('review-artifact', 'review-run', 'runtime.usage_review', str(path),
                 hashlib.sha256(path.read_bytes()).hexdigest(), 'application/json', 1, core.utcnow()))
    store(material)
    route = f"/api/v1/v6/model-calls/{call['id']}/reconcile"
    body = dict(artifact_id='review-artifact', confirmed=True)
    assert client.post(route, json={**body, 'confirmed':False}).status_code == 422
    for ambiguous in (1, 'true', None):
        assert client.post(route, json={**body, 'confirmed':ambiguous}).status_code == 422
    with core.connect() as db:
        db.execute("UPDATE artifacts SET run_id='other-run' WHERE id='review-artifact'")
    assert client.post(route, json=body).status_code == 409
    store(material)
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET run_id='nonexistent-run' WHERE id=?", (task['id'],))
    assert client.post(route, json=body).status_code == 409
    with core.connect() as db:
        db.execute("UPDATE agent_tasks SET run_id='review-run' WHERE id=?", (task['id'],))
    for invalid in (True, '10', -1, 1.5):
        store({**material, 'input_tokens':invalid})
        assert client.post(route, json=body).status_code == 409
    store(material)
    store({**material, 'provider_id':'another-provider'})
    assert client.post(route, json=body).status_code == 409
    store(material); path.write_text('{}')
    assert client.post(route, json=body).status_code == 409
    with core.connect() as db:
        assert _pending(db, 'task_id', task['id'])[0] == call['reserved_tokens']
        assert db.execute('SELECT COUNT(*) FROM model_usage_reconciliations_v6').fetchone()[0] == 0
    store(material)
    with core.connect() as db:
        db.execute("CREATE TRIGGER reject_review BEFORE INSERT ON model_usage_reconciliations_v6 "
                   "BEGIN SELECT RAISE(ABORT,'injected review write failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected review write failure'):
        client.post(route, json=body)
    with core.connect() as db:
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()[0] == 'unknown'
        assert db.execute('SELECT COUNT(*) FROM runtime_usage').fetchone()[0] == 0
        db.execute('DROP TRIGGER reject_review')
    import_route = f"/api/v1/v6/model-calls/{call['id']}/usage-review"
    assert client.post(import_route, json={**material, 'provider_id':'wrong'}).status_code == 409
    assert client.post(import_route, json={**material, 'raw_secret':'not-allowed'}).status_code == 422
    directory = Path(core.LOCAL_DATA_ROOT) / 'v6-usage-reviews'
    for table, condition in [('artifacts', "NEW.kind='runtime.usage_review'"),
                             ('v5_events', "NEW.event_type='call.review_imported'")]:
        with core.connect() as db:
            before_artifacts = db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]
            before_events = db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0]
            db.execute(f"CREATE TRIGGER fail_import BEFORE INSERT ON {table} WHEN {condition} "
                       "BEGIN SELECT RAISE(ABORT,'injected import failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='injected import failure'):
            client.post(import_route, json=material)
        assert list(directory.glob('*.json')) == []
        with core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == before_artifacts
            assert db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0] == before_events
            assert _pending(db, 'task_id', task['id'])[0] == call['reserved_tokens']
            db.execute('DROP TRIGGER fail_import')
    imported = client.post(import_route, json=material)
    assert imported.status_code == 201, imported.text
    assert imported.json()['consumption_state'] == 'unknown'
    assert 'uri' not in imported.json()
    imported_id = imported.json()['artifact_id']
    with core.connect() as db:
        imported_row = db.execute('SELECT * FROM artifacts WHERE id=?', (imported_id,)).fetchone()
        assert imported_row['run_id'] == 'review-run'
        assert hashlib.sha256(Path(imported_row['uri']).read_bytes()).hexdigest() == imported.json()['sha256']
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()[0] == 'unknown'
    # Use the imported material to settle, then test its current integrity.
    path = Path(imported_row['uri'])
    body = dict(artifact_id=imported_id, confirmed=True)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    assert client.post(route, json=body).json() == response.json()
    with core.connect() as db:
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()[0] == 'settled'
        assert db.execute('SELECT status FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()[0] == 'paused'
        assert _pending(db, 'task_id', task['id']) == (0,0)
        assert db.execute('SELECT input_tokens+output_tokens FROM runtime_usage').fetchone()[0] == 15
        assert db.execute('SELECT COUNT(*) FROM runtime_usage_reports').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM model_usage_reconciliations_v6')
    read_route = '/api/v1/v6/model-usage-reconciliations'
    page = client.get(read_route + '?limit=1').json()
    assert page['offset'] == 0 and page['has_more'] is False
    record = page['items'][0]
    assert record['integrity'] == 'intact'
    assert record['usage']['input_tokens'] == 10 and record['runtime_ms'] == 50
    assert 'uri' not in record
    assert client.get(read_route + '?offset=1').json()['items'] == []
    assert client.get(read_route + '?limit=101').status_code == 422
    def change_imported(value):
        path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')))
        with core.connect() as db:
            db.execute('UPDATE artifacts SET sha256=? WHERE id=?',
                       (hashlib.sha256(path.read_bytes()).hexdigest(), imported_id))
    change_imported({**material, 'input_tokens':11})
    assert client.post(route, json=body).status_code == 409
    assert client.get(read_route).json()['items'][0]['integrity'] == 'missing_or_changed'
    with core.connect() as db:
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?', (call['id'],)).fetchone()[0] == 'settled'
    change_imported(material)
    assert client.get(read_route).json()['items'][0]['integrity'] == 'intact'
    path.unlink()
    assert client.get(read_route).json()['items'][0]['integrity'] == 'missing_or_changed'


def test_actual_reconciled_overrun_is_not_clipped_and_blocks_next_budget(client):
    from fastapi import HTTPException
    from v5_runtime_calls import _spent
    cid, profile = setup(client, concurrency=2, hourly=100)
    task, decision = task_route(client, cid, profile, 'overrun')
    call = reserve_call(decision['id'], task['id'], 'call-runner', task['attempt'])
    start_call(call['id']); fail_call(call['id'])
    ready = create_ready(client, target='https://overrun-review.example.test')
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('overrun-run', ready['id'], 'traditional', ready['current_scope_snapshot_id'], ready['current_policy_id'],
             'completed', 'report', 1, None, None, None, None, core.utcnow()))
        db.execute("UPDATE agent_tasks SET run_id='overrun-run',status='paused' WHERE id=?", (task['id'],))
    material = dict(call_id=call['id'], decision_id=decision['id'], provider_id=call['provider_id'],
        review_kind='provider_record', input_tokens=100, output_tokens=25, cost_micros=0,
        runtime_ms=call['max_runtime_ms'] + 1)
    imported = client.post(f"/api/v1/v6/model-calls/{call['id']}/usage-review", json=material)
    assert imported.status_code == 201
    response = client.post(f"/api/v1/v6/model-calls/{call['id']}/reconcile",
        json=dict(artifact_id=imported.json()['artifact_id'], confirmed=True))
    assert response.status_code == 200, response.text
    audit = client.get('/api/v1/v6/model-usage-reconciliations').json()['items'][0]
    assert audit['integrity'] == 'intact'
    assert audit['usage']['input_tokens'] + audit['usage']['output_tokens'] == 125
    assert audit['reservation_exceeded'] == dict(tokens=True, cost=False, runtime=True)
    with core.connect() as db:
        assert _spent(db, 'profile_id', profile)[0] == 125
        assert _pending(db, 'profile_id', profile)[0] == 0
    next_task, next_route = task_route(client, cid, profile, 'after-overrun')
    with pytest.raises(HTTPException, match='token budget exhausted'):
        reserve_call(next_route['id'], next_task['id'], 'call-runner', next_task['attempt'])
