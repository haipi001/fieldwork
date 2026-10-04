"""Draft rules and explicitly reviewed replay over real loopback HTTP.

Candidate construction and account credential supply are fixture inputs.
These tests do not establish autonomous discovery or independent semantic verification.
"""
import copy

import pytest

import final_core as core
import guided_research as guided
import traditional_runtime as http
from tests.test_final import client
from tests.test_guided_research import wait
from tests.test_http_business_boundary import object_server


def draft(client, target='https://example.test'):
    response = client.post('/api/v1/engagements', json={'name': 'Rules fixture', 'target': target})
    assert response.status_code == 201
    return response.json()


def rules_payload(project, target='https://example.test/object', access='owner_only'):
    return {'scope_snapshot_id': project['current_scope_snapshot_id'], 'allow_authentication': True,
            'rules': [{'target': target, 'access': access, 'source': 'Project specification section 4',
                       'allowed_principals': ['B'] if access == 'allowlist' else []}]}


def test_draft_rules_are_versioned_frozen_and_stale_review_rejected(client):
    project = draft(client)
    original = project['current_scope_snapshot_id']
    payload = rules_payload(project)
    updated = client.put(f"/api/v1/engagements/{project['id']}/http-read-rules", json=payload)
    assert updated.status_code == 200
    revised = updated.json()
    assert revised['current_scope_snapshot_id'] != original
    assert revised['confirmed_at'] is None and revised['status'] == 'draft'
    assert revised['scope']['auth_allowed_hosts'] == ['example.test']
    assert revised['scope']['denied_actions'] == project['scope']['denied_actions']
    with core.connect() as db:
        snapshots = db.execute('SELECT version,rules,confirmed_at FROM scope_snapshots WHERE engagement_id=? ORDER BY version', (project['id'],)).fetchall()
    assert len(snapshots) == 2 and snapshots[0]['version'] == 1
    assert core.load(snapshots[0]['rules']) == project['scope']
    assert client.put(f"/api/v1/engagements/{project['id']}/http-read-rules", json=payload).status_code == 409
    assert client.post(f"/api/v1/engagements/{project['id']}/confirm", json={'scope_snapshot_id': original}).status_code == 409
    frozen = client.post(f"/api/v1/engagements/{project['id']}/confirm", json={'scope_snapshot_id': revised['current_scope_snapshot_id']})
    assert frozen.status_code == 200 and frozen.json()['confirmed_at']
    payload['scope_snapshot_id'] = revised['current_scope_snapshot_id']
    assert client.put(f"/api/v1/engagements/{project['id']}/http-read-rules", json=payload).status_code == 409


@pytest.mark.parametrize('change', ['external', 'prefix_host', 'duplicate', 'credentials', 'query', 'fragment', 'conflict', 'blank_source', 'blank_principal', 'bad_permission'])
def test_invalid_rules_do_not_change_scope(client, change):
    project = draft(client)
    payload = rules_payload(project)
    rule = payload['rules'][0]
    if change == 'external': rule['target'] = 'https://outside.test/object'
    elif change == 'prefix_host': rule['target'] = 'https://example.test.evil/object'
    elif change == 'duplicate': payload['rules'].append(copy.deepcopy(rule))
    elif change == 'credentials': rule['target'] = 'https://user:password@example.test/object'
    elif change == 'query': rule['target'] += '?token=fixture'
    elif change == 'fragment': rule['target'] += '#object'
    elif change == 'conflict': rule['allowed_principals'] = ['B']
    elif change == 'blank_source': rule['source'] = '   '
    elif change == 'blank_principal': rule.update(access='allowlist', allowed_principals=['  '])
    else: rule['access'] = 'guess'
    assert client.put(f"/api/v1/engagements/{project['id']}/http-read-rules", json=payload).status_code == 422
    assert core.get_engagement(project['id'])['current_scope_snapshot_id'] == project['current_scope_snapshot_id']


def prepared_live_candidate(client, monkeypatch, object_server, access='owner_only'):
    origin, calls = object_server
    response = client.post('/api/v1/engagements', json={
        'name': 'Live V5 workflow fixture', 'target': origin,
        'scope': {'allow_private_ips': True}, 'policy': {'max_requests_per_second': 1000},
    })
    project = response.json()
    payload = rules_payload(project, origin + '/object', access)
    saved = client.put(f"/api/v1/engagements/{project['id']}/http-read-rules", json=payload)
    assert saved.status_code == 200
    project = saved.json()
    project = client.post(f"/api/v1/engagements/{project['id']}/confirm", json={'scope_snapshot_id': project['current_scope_snapshot_id']}).json()
    run_id = core.uid('run')
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, project['id'], 'traditional', project['current_scope_snapshot_id'], project['current_policy_id'],
                    'completed', 'report', 0, None, None, None, None, core.utcnow()))
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)', (run_id, 30, 0, 10, 0, 0, 0, core.utcnow()))
    identities = []
    for role in ('owner', 'other'):
        response = client.post(f"/api/v1/engagements/{project['id']}/identities", json={
            'label': role, 'role': 'user', 'session_status': 'ready', 'credential_ref': 'fixture://' + role})
        assert response.status_code == 201
        identities.append(response.json()['id'])
    roles = dict(zip(identities, ('owner', 'other')))
    monkeypatch.setattr(http, 'resolve_identity_headers', lambda identity: {'X-Role': roles[identity]})
    for path, identity in [('/object', identities[0]), ('/me', identities[0]), ('/me', identities[1])]:
        result = http.create_http_exchange(run_id, http.ExchangeRequestInput(url=origin + path, identity_id=identity))
        assert result['response_status'] == 200
    observation = client.post(f'/api/v1/runs/{run_id}/observations', json={
        'observation_type': 'authorization', 'subject': origin + '/object',
        'summary': 'Fixture object read material collected over real HTTP', 'source_capability': 'fixture',
    }).json()
    candidate = client.post(f'/api/v1/runs/{run_id}/candidates', json={
        'title': 'Cross identity read fixture', 'category': 'CWE-639', 'target': origin + '/object',
        'hypothesis': 'Verify project object read permission', 'observation_ids': [observation['id']],
    }).json()
    return run_id, candidate, project, calls


def workflow_path(run_id, candidate):
    return f"/api/v1/runs/{run_id}/candidate-workflow/{candidate['id']}"


def test_reviewed_live_replay_requires_authorization_and_preserves_unconfirmed_result(client, monkeypatch, object_server):
    run_id, candidate, project, calls = prepared_live_candidate(client, monkeypatch, object_server)
    path = workflow_path(run_id, candidate)
    plan = client.get(path + '/execution-plan').json()
    assert plan['can_execute'] and plan['request_count'] == 10 and plan['remaining_requests'] == 27
    assert plan['sends_requests'] is False and len(calls) == 3
    assert 'X-Role' not in str(plan)
    assert client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint']}).status_code == 422
    response = client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True})
    assert response.status_code == 202
    job = wait(client, response.json())
    assert job['result']['requests_sent'] == 10 and len(calls) == 13
    assert job['result']['items'][0]['auto_verification']['status'] == 'reproduced'
    assert job['result']['items'][0]['auto_verification']['business_boundary']['status'] == 'denied'
    persisted = client.get(f'/api/v1/runs/{run_id}/candidate-workflow').json()['execution_job']
    assert persisted['id'] == job['id'] and persisted['status'] == job['status']
    assert persisted['result']['reviewed_execution'] is True
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0] == 0
        import json
        artifact = db.execute("SELECT uri FROM artifacts WHERE kind='http.replay'").fetchone()
        from pathlib import Path
        replay = json.loads(Path(artifact['uri']).read_text())
        responses = [response for group in replay['rounds'] for response in group.values()]
        assert len(responses) == 10
        assert all(response['process_execution']['file_read_denied'] for response in responses)
        assert len({response['process_execution']['child_pid'] for response in responses}) == 10


@pytest.mark.parametrize('change', ['shared', 'missing_rule', 'budget', 'stale_evidence', 'expired_identity', 'new_scope'])
def test_blocked_or_changed_plan_sends_no_replay_requests(client, monkeypatch, object_server, change):
    run_id, candidate, project, calls = prepared_live_candidate(client, monkeypatch, object_server,
                                                              'allowlist' if change == 'shared' else 'owner_only')
    path = workflow_path(run_id, candidate)
    plan = client.get(path + '/execution-plan').json()
    with core.connect() as db:
        if change == 'missing_rule':
            scope = {**project['scope'], 'http_object_read_rules': []}
            db.execute('UPDATE scope_snapshots SET rules=? WHERE id=?', (core.dump(scope), project['current_scope_snapshot_id']))
        elif change == 'budget': db.execute('UPDATE run_budgets_v2 SET requests_used=29 WHERE run_id=?', (run_id,))
        elif change == 'stale_evidence': db.execute("UPDATE observations SET summary='Changed claim material' WHERE run_id=?", (run_id,))
        elif change == 'expired_identity': db.execute("UPDATE identity_profiles SET session_status='expired'")
        elif change == 'new_scope': db.execute("UPDATE analysis_runs SET scope_snapshot_id='outdated-scope' WHERE id=?", (run_id,))
    response = client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True})
    assert response.status_code == 409 and len(calls) == 3
    if change == 'shared': assert 'business_access_permitted' in plan['blockers']
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0] == 0


def test_cancel_before_first_request_and_changed_material_during_replay(client, monkeypatch, object_server):
    run_id, candidate, project, calls = prepared_live_candidate(client, monkeypatch, object_server)
    path = workflow_path(run_id, candidate)
    captured = []
    monkeypatch.setattr(guided, 'dispatch', lambda job_id, data: captured.append((job_id, data)))
    plan = client.get(path + '/execution-plan').json()
    response = client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True})
    assert response.status_code == 202
    job_id, data = captured.pop()
    assert client.post(f'/api/v1/guided-research/{job_id}/cancel').status_code == 200
    guided.run_job(job_id, data)
    assert client.get(f'/api/v1/guided-research/{job_id}').json()['status'] == 'cancelled'
    assert len(calls) == 3
    plan = client.get(path + '/execution-plan').json()
    response = client.post(path + '/execute', json={'source_fingerprint': plan['source_fingerprint'], 'authorized': True})
    job_id, data = captured.pop()
    import v5_http_transport as transport
    original = transport.request_once
    def changing(spec, addresses, check_current=None):
        result = original(spec, addresses, check_current)
        with core.connect() as db:
            db.execute("UPDATE observations SET summary='Modified during replay' WHERE run_id=?", (run_id,))
        return result
    monkeypatch.setattr(transport, 'request_once', changing)
    guided.run_job(job_id, data)
    result = client.get(f'/api/v1/guided-research/{job_id}').json()
    assert len(calls) == 4 and result['result']['requests_sent'] == 1
    assert result['status'] == 'awaiting_input'
    assert not result['result']['verification_executed']
