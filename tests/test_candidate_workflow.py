import copy
import json

import pytest

import final_core as core
import guided_research as guided
from candidate_workflow import annotate
from tests.test_guided_research import client, candidate, wait


def test_preview_is_read_only_and_reports_blockers(client, monkeypatch):
    run, c = candidate(client)
    monkeypatch.setattr(guided, 'dispatch', lambda *a: pytest.fail('preview dispatched'))
    before = client.get(f"/api/v1/candidates/{c['id']}").json()['candidate']
    response = client.get(f"/api/v1/runs/{run['id']}/candidate-workflow")
    assert response.status_code == 200
    result = response.json()
    assert result['counts'] == {'blocked': 1}
    assert result['automatic_execution'] is False
    assert 'identities' in result['items'][0]['triage']['blockers']
    assert client.get(f"/api/v1/candidates/{c['id']}").json()['candidate'] == before
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM guided_research_jobs').fetchone()[0] == 0


def test_information_retained_without_dispatch_or_false_verdict(client, monkeypatch):
    import guided_http
    run, c = candidate(client, category='business/offering')
    monkeypatch.setattr(guided_http, 'execute', lambda *a: pytest.fail('observation dispatched'))
    job = wait(client, client.post(f"/api/v1/runs/{run['id']}/guided-research", json={'execute_ready': True}).json())
    assert job['result']['triage_counts'] == {'observation': 1}
    assert job['result']['items'][0]['triage']['verdict'] == 'unconfirmed'
    assert job['result']['requests_sent'] == 0
    assert client.get(f"/api/v1/candidates/{c['id']}").json()['candidate']['status'] == 'candidate'


def test_missing_reference_blocks_even_with_related_material_and_binding(client, monkeypatch):
    import guided_http
    run, c = candidate(client)
    with core.connect() as db:
        db.execute('UPDATE candidate_findings SET evidence_ids=? WHERE id=?',
                   (json.dumps(c['evidence_ids'] + ['missing-evidence']), c['id']))
    monkeypatch.setattr(guided_http, 'plan', lambda *a: {'binding_hash': 'fixture'})
    monkeypatch.setattr(guided_http, 'execute', lambda *a: pytest.fail('missing evidence dispatched'))
    job = wait(client, client.post(f"/api/v1/runs/{run['id']}/guided-research", json={'execute_ready': True}).json())
    item = job['result']['items'][0]
    assert item['sources'] and item['draft']['http_binding']
    assert item['triage']['lane'] == 'needs_evidence'
    assert 'linked_evidence' in item['triage']['blockers']
    assert job['result']['requests_sent'] == 0


def test_exact_duplicates_are_batch_local_and_do_not_change_source():
    records = [dict(id='a', run_id='run', engagement_id='project', mode='traditional',
                    category='authorization', target='/object', title='Claim', hypothesis='Claim',
                    status='candidate', evidence_ids=['e'])]
    records += [{**records[0], 'id': 'b'}, {**records[0], 'id': 'c', 'run_id': 'other'}]
    original = copy.deepcopy(records)
    items = [dict(candidate_id=r['id'], draft={'verification_method': 'http_object_read',
             'http_binding': {'binding_hash': 'binding'}}, sources=[{'id': 'e'}], gaps=[]) for r in records]
    assert annotate(records, items) == {'verification_ready': 2, 'duplicate': 1}
    assert items[1]['triage']['duplicate_of'] == 'a'
    assert not items[1]['triage']['dispatch_eligible']
    assert records == original


def test_exact_duplicate_dispatches_once_and_persists_reason(client, monkeypatch):
    import guided_http
    run, c = candidate(client)
    with core.connect() as db:
        row = dict(db.execute('SELECT * FROM candidate_findings WHERE id=?', (c['id'],)).fetchone())
        row['id'] = 'duplicate-fixture'
        db.execute('INSERT INTO candidate_findings (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', list(row.values()))
    original = guided.material_for
    def material(record, data):
        item = original(record, data)
        item['gaps'] = []
        item['draft']['http_binding'] = {'binding_hash': 'isolated-fixture'}
        return item
    monkeypatch.setattr(guided, 'material_for', material)
    calls = []
    def execute(record, binding, before, after):
        calls.append(record['id'])
        return {'status': 'not_established', 'binding_hash': binding['binding_hash']}
    monkeypatch.setattr(guided_http, 'execute', execute)
    job = wait(client, client.post(f"/api/v1/runs/{run['id']}/guided-research", json={'execute_ready': True}).json())
    assert len(calls) == 1
    assert job['result']['triage_counts'] == {'verification_ready': 1, 'duplicate': 1}
    assert all(item['triage']['verdict'] == 'unconfirmed' for item in job['result']['items'])
    repeated = client.post(f"/api/v1/runs/{run['id']}/guided-research", json={'execute_ready': True}).json()
    assert repeated['id'] == job['id'] and len(calls) == 1


@pytest.mark.parametrize('changed', ['evidence', 'claim', 'scope', 'policy'])
def test_verification_reuse_requires_current_inputs(client, changed):
    run, c = candidate(client)
    data = guided.snapshot(run['id'])
    record = data['candidates'][0]
    binding = {'binding_hash': 'same-binding'}
    before = guided.verification_input_digest(record, data, binding)
    updated = copy.deepcopy(data)
    if changed == 'evidence':
        updated['evidence'][0]['summary'] = 'Changed observation evidence'
    elif changed == 'claim':
        updated['candidates'][0]['hypothesis'] = 'Different security claim'
    else:
        updated['runtime_context'][changed + '_digest'] = 'changed'
    assert guided.verification_input_digest(updated['candidates'][0], updated, binding) != before
    # Spending budget does not itself invalidate otherwise identical evidence.
    data['runtime_context']['budget'] = {'requests_used': 99}
    assert guided.verification_input_digest(record, data, binding) == before
