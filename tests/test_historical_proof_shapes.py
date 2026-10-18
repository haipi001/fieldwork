"""Corrupt persisted proof data remains reviewable and cannot export a capsule."""
import pytest
from fastapi import HTTPException

import final_core as core
from tests.test_final import client
from tests.test_v5_team_plan import _setup


@pytest.fixture
def historical(client):
    engagement, _, run_id = _setup(client)
    now = core.utcnow()
    verification = {'receipt_id': 'shape-receipt', 'poc_artifact_ids': ['shape-artifact'],
                    'steps': ['Fixture step'], 'oracle': 'fixture'}
    with core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed' WHERE id=?", (run_id,))
        db.execute('INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('shape-candidate', run_id, engagement['id'], 'traditional', 'Historical fixture',
                    'fixture', 'https://team.example.test', 'fixture', 'verified', '[]', now, now))
        db.execute('INSERT INTO canonical_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('shape-finding', 'shape-candidate', engagement['id'], 'traditional', 'Historical fixture',
                    'fixture', 'unknown', 'https://team.example.test', '{}', '{}', core.dump(verification),
                    '[]', 'verified', now, now))
        db.execute('INSERT INTO verification_attempts VALUES(?,?,?,?,?,?,?,?)',
                   ('shape-receipt', 'shape-candidate', 'fixture', 'machine_receipt', 1,
                    core.dump({'artifacts': {'shape-artifact': '0' * 64}}), now, now))
    return run_id


def assert_review(client, run_id):
    response = client.get(f'/api/v1/runs/{run_id}/details')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['proof_status']['proof_checked'] == 0
    assert value['proof_status']['review_required'] == 1
    assert next(item for item in value['stages'] if item['id'] == 'verification')['status'] == 'human_review'
    capsule = client.get('/api/v1/findings/shape-finding/proof-capsule')
    assert capsule.status_code == 409, capsule.text
    with core.connect() as db:
        assert db.execute("SELECT status FROM canonical_findings WHERE id='shape-finding'").fetchone()[0] == 'verified'


@pytest.mark.parametrize('field,value', [
    ('impact', 'null'), ('eligibility', '[]'), ('verification', 'null'),
    ('verification', '[]'), ('verification', '42'), ('verification', '{'),
    ('verification', b'\xff'), ('verification', '[' * 1200 + '0' + ']' * 1200),
    ('evidence_ids', 'null'), ('evidence_ids', '{}'), ('evidence_ids', '[{}]'),
    ('evidence_ids', '["deleted-evidence"]'),
])
def test_malformed_finding_fields_stay_pending_review(client, historical, field, value):
    with core.connect() as db:
        db.execute(f'UPDATE canonical_findings SET {field}=? WHERE id=?', (value, 'shape-finding'))
    assert_review(client, historical)


@pytest.mark.parametrize('value', [None, [], 'unexpected', 42,
                                  {'artifacts': []}, {'artifacts': {'shape-artifact': None}},
                                  {'v5_verification_receipt_id': ['bad'], 'proof': {}},
                                  {'v5_verification_receipt_id': 'receipt', 'proof': None}])
def test_malformed_machine_receipt_stays_pending_review(client, historical, value):
    with core.connect() as db:
        db.execute('UPDATE verification_attempts SET result=? WHERE id=?', (core.dump(value), 'shape-receipt'))
    assert_review(client, historical)


@pytest.mark.parametrize('field,value', [('receipt_id', {}), ('poc_artifact_ids', [{}]),
                                        ('poc_artifact_ids', None), ('steps', None),
                                        ('steps', [{}]), ('oracle', {})])
def test_malformed_nested_verification_stays_pending_review(client, historical, field, value):
    with core.connect() as db:
        verification = core.load(db.execute("SELECT verification FROM canonical_findings WHERE id='shape-finding'").fetchone()[0])
        verification[field] = value
        db.execute('UPDATE canonical_findings SET verification=? WHERE id=?', (core.dump(verification), 'shape-finding'))
    assert_review(client, historical)


def test_proof_export_rejects_scope_from_another_project(client, historical):
    from tests.test_final import create_ready
    other = create_ready(client)
    finding, evidence, _ = core.finding_report_inputs('shape-finding')
    with pytest.raises(HTTPException) as error:
        core.build_proof_attachments(finding, evidence, other['current_scope_snapshot_id'])
    assert error.value.status_code == 409
