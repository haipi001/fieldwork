import json
from pathlib import Path
import pytest
import final_core
from tests.test_final import client
from tests.test_v5_intelligence import campaign, import_osv, _trivy_inventory_artifact


def setup(client,version='1.2.3',synthetic=False):
    engagement,cid=campaign(client)
    import_osv(client)
    artifact,node=_trivy_inventory_artifact(client,engagement,cid,version=version,synthetic=synthetic)
    response=client.post('/api/v1/intelligence/fingerprint',json={'campaign_id':cid,'packages':[{
        'ecosystem':'PyPI','name':'example-pkg','version':'1.2.3','source_kind':'artifact','source_ref':artifact}]})
    assert response.status_code==200,response.text
    return response.json()['matches'][0],artifact


def test_package_plan_is_readonly_commit_is_atomic_idempotent_and_independently_replayed(client):
    match,artifact=setup(client)
    url=f"/api/v1/intelligence/matches/{match['id']}"
    with final_core.connect() as db:
        before=db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]
    plan=client.get(url+'/verification-plan')
    assert plan.status_code==200,plan.text
    value=plan.json()
    assert value['verification_domain']=='applicability_only' and value['target_requests']==0
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==before
        assert db.execute('SELECT count(*) FROM verification_requests_v5').fetchone()[0]==0
    commit=client.post(url+'/verification-request',json={'preview_hash':value['preview_hash']})
    assert commit.status_code==202,commit.text
    assert commit.json()['created'] is True and commit.json()['execution_started'] is False
    again=client.post(url+'/verification-request',json={'preview_hash':value['preview_hash']})
    assert again.status_code==202 and again.json()['created'] is False
    assert again.json()['request']['id']==commit.json()['request']['id']
    rid=commit.json()['request']['id']
    assert client.get('/api/v1/verification/requests/'+rid).json()['latest_receipt_id'] is None
    tick=client.post('/api/v1/verification/local/tick',params={'request_id':rid})
    assert tick.status_code==200,tick.text
    completed=tick.json()['completed']
    assert len(completed)==1 and completed[0]['status']=='succeeded'
    receipt=client.get('/api/v1/verification/receipts/'+completed[0]['receipt_id']).json()
    status=client.get('/api/v1/verification/requests/'+rid).json()
    assert status['latest_receipt_id']==receipt['id'] and status['task_status']=='succeeded'
    assert receipt['integrity']['promotion_domain']=='applicability_only'
    assert receipt['environment']['process_isolation']['network_denied'] is True
    confirmation={'claim_node_id':commit.json()['claim_id'],'receipt_id':receipt['id']}
    first=client.post(url+'/verify',json=confirmation)
    assert first.status_code==200,first.text
    second=client.post(url+'/verify',json=confirmation)
    assert second.status_code==200 and second.json()['updated_at']==first.json()['updated_at']
    assert second.json()['rationale']['verification_receipt_id']==receipt['id']
    with final_core.connect() as db:
        assert db.execute("SELECT count(*) FROM v5_events WHERE entity_id=? AND event_type='match.verified'",(match['id'],)).fetchone()[0]==1
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        db.execute("UPDATE research_nodes SET body='changed after confirmation' WHERE id=?",(match['rationale']['advisory_node_id'],))
    stale_confirmation=client.post(url+'/verify',json=confirmation)
    assert stale_confirmation.status_code==409
    with final_core.connect() as db:
        assert db.execute("SELECT count(*) FROM v5_events WHERE entity_id=? AND event_type='match.verified'",(match['id'],)).fetchone()[0]==1


@pytest.mark.parametrize('case',['stale_hash','artifact_changed','advisory_node_changed','synthetic','mismatched_package','invalid_tool_output','invalid_inventory'])
def test_package_plan_rejects_changed_or_unusable_materials_without_partial_claims(client,case):
    match,artifact=setup(client,version='9.9.9' if case=='mismatched_package' else '1.2.3',synthetic=case=='synthetic')
    url=f"/api/v1/intelligence/matches/{match['id']}"
    if case in {'invalid_tool_output','invalid_inventory'}:
        import hashlib
        with final_core.connect() as db:
            path=Path(db.execute('SELECT uri FROM artifacts WHERE id=?',(artifact,)).fetchone()[0])
            source=json.loads(path.read_text())
            if case=='invalid_tool_output':source['tool_result']['status']='failed'
            else:source['observations'].append({'observation_type':'code.dependency_inventory_incomplete','structured':{}})
            path.write_text(json.dumps(source))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            db.execute('UPDATE artifacts SET sha256=? WHERE id=?',(digest,artifact))
            row=db.execute("SELECT id,attributes_json FROM research_nodes WHERE source_type='artifact' AND source_ref=?",(artifact,)).fetchone()
            attrs=json.loads(row['attributes_json']);attrs['sha256']=digest
            db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(json.dumps(attrs),row['id']))
    preview=client.get(url+'/verification-plan')
    if case=='synthetic':
        assert preview.status_code==409
        return
    assert preview.status_code==200,preview.text
    with final_core.connect() as db:
        before=db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]
        if case=='artifact_changed':
            path=db.execute('SELECT uri FROM artifacts WHERE id=?',(artifact,)).fetchone()[0]
            Path(path).write_text('{}')
        elif case=='advisory_node_changed':
            db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(match['rationale']['advisory_node_id'],))
    response=client.post(url+'/verification-request',json={'preview_hash':'0'*64 if case=='stale_hash' else preview.json()['preview_hash']})
    if case=='mismatched_package':
        # A structurally valid contract is replayed independently; mismatch cannot yield positive applicability.
        assert response.status_code==202,response.text
        tick=client.post('/api/v1/verification/local/tick',params={'request_id':response.json()['request']['id']}).json()
        receipt=client.get('/api/v1/verification/receipts/'+tick['completed'][0]['receipt_id']).json()
        assert receipt['result']['status']!='verified'
    else:
        assert response.status_code==409,response.text
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==before
            assert db.execute('SELECT count(*) FROM verification_requests_v5').fetchone()[0]==0
