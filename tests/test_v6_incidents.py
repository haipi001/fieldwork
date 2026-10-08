import sqlite3
import hashlib
from pathlib import Path

import pytest

import final_core as core
from tests.test_agent_audit import client, fixture, create, upload, analyze


def test_response_state_requires_current_state_containment_and_intact_evidence(client, fixture):
    audit_id = create(client, fixture)
    for body in fixture['imports']:
        upload(client, audit_id, body)
    analyze(client, audit_id)
    with core.connect() as db:
        incident = db.execute('SELECT i.candidate_id,c.run_id,e.artifact_id,a.uri FROM agent_incidents i '
            'JOIN candidate_findings c ON c.id=i.candidate_id JOIN agent_events e ON e.id=i.event_id '
            'JOIN artifacts a ON a.id=e.artifact_id WHERE i.audit_id=? LIMIT 1', (audit_id,)).fetchone()
    route = f"/api/v1/v6/incidents/{incident['candidate_id']}/response"
    def advance(previous, state):
        return client.post(route, json=dict(expected_state=previous, state=state, artifact_id=incident['artifact_id']))
    assert client.get(route).json()['state'] == 'DETECTED'
    assert client.get(route).json()['response_evidence_integrity'] == 'not_recorded'
    assert advance('DETECTED', 'RECOVERED').status_code == 409
    assert advance('DETECTED', 'TRIAGED').status_code == 200
    assert advance('TRIAGED', 'CONTAINED').status_code == 409
    assert client.post(f"/api/v1/v6/runs/{incident['run_id']}/contain", json={'reason_code': 'compromised'}).status_code == 200
    assert advance('TRIAGED', 'CONTAINED').status_code == 200
    assert advance('TRIAGED', 'CONTAINED').status_code == 409
    assert advance('CONTAINED', 'INVESTIGATING').status_code == 200
    original = Path(incident['uri']).read_bytes()
    Path(incident['uri']).write_text('{}')
    changed = client.get(route).json()
    assert changed['state'] == 'INVESTIGATING'
    assert changed['response_evidence_integrity'] == 'missing_or_changed'
    assert all(row['evidence_integrity'] == 'missing_or_changed' for row in changed['history'])
    replacement = Path(incident['uri']).with_name('response-review.json')
    replacement.write_text('{"review":"new intact evidence"}')
    with core.connect() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
            ('new-review', incident['run_id'], 'incident.review', str(replacement),
             hashlib.sha256(replacement.read_bytes()).hexdigest(), 'application/json', 1, core.utcnow()))
    blocked = client.post(route, json=dict(expected_state='INVESTIGATING', state='REMEDIATING', artifact_id='new-review'))
    assert blocked.status_code == 409
    assert 'history evidence' in blocked.json()['detail']
    assert client.get(route).json()['state'] == 'INVESTIGATING'
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM incident_response_events_v6').fetchone()[0] == 3
    assert advance('INVESTIGATING', 'REMEDIATING').status_code == 409
    Path(incident['uri']).write_bytes(original)
    assert client.get(route).json()['response_evidence_integrity'] == 'intact'
    for previous, state in [('INVESTIGATING', 'REMEDIATING'), ('REMEDIATING', 'RECOVERED'), ('RECOVERED', 'CLOSED')]:
        assert advance(previous, state).status_code == 200
    assert client.get(route).json()['run_containment_active'] is True
    assert advance('CLOSED', 'DETECTED').status_code == 409
    with core.connect() as db:
        stored_hash = db.execute('SELECT sha256 FROM artifacts WHERE id=?', (incident['artifact_id'],)).fetchone()[0]
        db.execute('UPDATE artifacts SET sha256=? WHERE id=?', ('0' * 64, incident['artifact_id']))
    assert client.get(route).json()['response_evidence_integrity'] == 'missing_or_changed'
    with core.connect() as db:
        db.execute('UPDATE artifacts SET sha256=? WHERE id=?', (stored_hash, incident['artifact_id']))
    assert client.get(route).json()['response_evidence_integrity'] == 'intact'
    Path(incident['uri']).unlink()
    assert client.get(route).json()['response_evidence_integrity'] == 'missing_or_changed'
    from tests.test_final import create_ready
    ready = create_ready(client, target='https://incident-release.example.test')
    with core.connect() as db:
        db.execute('UPDATE analysis_runs SET engagement_id=?,scope_snapshot_id=?,policy_id=? WHERE id=?',
            (ready['id'], ready['current_scope_snapshot_id'], ready['current_policy_id'], incident['run_id']))
        generation = db.execute('SELECT MAX(id) FROM run_containment_events_v6 WHERE run_id=?',
                                (incident['run_id'],)).fetchone()[0]
    release_route = f"/api/v1/v6/runs/{incident['run_id']}/release-containment"
    release_body = dict(expected_generation=generation, artifact_id='new-review')
    blocked_release = client.post(release_route, json=release_body)
    assert blocked_release.status_code == 409
    assert 'incident response evidence' in blocked_release.json()['detail']
    assert client.get(route).json()['run_containment_active'] is True
    Path(incident['uri']).write_bytes(original)
    released = client.post(release_route, json=release_body)
    assert released.status_code == 200, released.text
    assert released.json()['active'] is False
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM incident_response_events_v6').fetchone()[0] == 6
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM incident_response_events_v6')
