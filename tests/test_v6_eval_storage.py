import hashlib
import json
import sqlite3

import pytest

import final_core as core
from tests.test_final import client, create_ready
from v6_evals import record_eval_run, register_scenario


def test_eval_records_bind_fixture_artifact_and_compare_baseline(client, tmp_path):
    project = create_ready(client)
    manifest = dict(id='local-safety', version='1', pack_id='agent', category='safety',
                    objective='Measure bounded local fixture', expected={'false_verified': 0},
                    budgets={}, allowed_capabilities=['network.request'], forbidden_capabilities=['network.write'])
    subject = dict(model=None, profile=None, prompt_sha256=None, policy_sha256=None,
                   pack_version='1', scheduler={'logical_concurrency': 1})
    metrics = dict(false_verified=0, policy_bypasses=0, candidate_precision=1,
                   replay_success=1, cost_usd=0, evidence_gain=1)
    path = tmp_path / 'captured.json'
    path.write_text(json.dumps(dict(scenario_id='local-safety', scenario_version='1',
                                    subject=subject, metrics=metrics)))
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('eval-fixture-run', project['id'], 'traditional', project['current_scope_snapshot_id'],
                    project['current_policy_id'], 'completed', 'report', 1, None, None, None, None, core.utcnow()))
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                   ('eval-artifact', 'eval-fixture-run', 'eval.capture', str(path),
                    hashlib.sha256(path.read_bytes()).hexdigest(), 'application/json', 1, core.utcnow()))
        register_scenario(db, manifest)
        register_scenario(db, manifest)
        with pytest.raises(ValueError, match='different contents'):
            register_scenario(db, {**manifest, 'objective': 'changed'})
        baseline = record_eval_run(db, scenario_id='local-safety', scenario_version='1', run_id='eval-fixture-run',
                                   artifact_id='eval-artifact', subject=subject, metrics=metrics)
        current = record_eval_run(db, scenario_id='local-safety', scenario_version='1', run_id='eval-fixture-run',
                                  artifact_id='eval-artifact', subject=subject, metrics=metrics, baseline_id=baseline)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM eval_runs_v6 WHERE id=?', (current,))
        with pytest.raises(ValueError, match='conflict'):
            record_eval_run(db, scenario_id='local-safety', scenario_version='1', run_id='eval-fixture-run',
                            artifact_id='eval-artifact', subject=subject, metrics={**metrics, 'false_verified': 1})
    rows = client.get('/api/v1/v6/eval-runs').json()['runs']
    assert len(rows) == 2 and next(row for row in rows if row['id'] == current)['result']['comparison_available']
    assert client.get('/api/v1/v6/eval-scenarios').json()['scenarios'] == [manifest]
    assert client.post('/api/v1/v6/eval-runs', json={}).status_code == 405
    path.write_text('{}')
    invalid = client.get('/api/v1/v6/eval-runs').json()['runs']
    assert all(row['status'] == 'invalid' and row['integrity']['artifact'] is False for row in invalid)
    with core.connect() as db, pytest.raises(ValueError, match='changed'):
        record_eval_run(db, scenario_id='local-safety', scenario_version='1', run_id='eval-fixture-run',
                        artifact_id='eval-artifact', subject=subject, metrics=metrics)
