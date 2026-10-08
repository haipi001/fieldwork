import json

import final_core as core
from tests.test_final import client, create_ready
import v6_eval_seeds as seeds


def test_policy_seed_records_actual_decisions_and_detects_bypass(client, tmp_path, monkeypatch):
    project = create_ready(client)
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('seed-run', project['id'], 'traditional', project['current_scope_snapshot_id'],
             project['current_policy_id'], 'completed', 'report', 1, None, None, None, None, core.utcnow()))
        baseline = seeds.run_policy_seed(db, 'seed-run', tmp_path)
        row = db.execute('SELECT * FROM eval_runs_v6 WHERE id=?', (baseline,)).fetchone()
        assert json.loads(row['result_json'])['passed']
        assert json.loads(row['metrics_json']) == dict(case_count=12, decision_mismatches=0, policy_bypasses=0)
        monkeypatch.setattr(seeds, 'evaluate_policy', lambda _: ('allow', ['injected.bypass']))
        failed = seeds.run_policy_seed(db, 'seed-run', tmp_path, baseline)
        result = json.loads(db.execute('SELECT result_json FROM eval_runs_v6 WHERE id=?', (failed,)).fetchone()[0])
        assert not result['passed'] and 'policy_bypasses' in result['failures']
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0] == 0
    assert len(client.get('/api/v1/v6/eval-runs').json()['runs']) == 2
