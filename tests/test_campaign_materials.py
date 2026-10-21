from pathlib import Path
import hashlib
import pytest
import final_core as core
import campaign_materials as materials
from tests.test_final import client
from tests.test_v5_team_plan import _setup


def fixture_materials(client):
    engagement,campaign,run=_setup(client)
    path=core.LOCAL_DATA_ROOT/'recorded.json';path.write_bytes(b'{"recorded":"bounded local evidence"}')
    aid,oid,eid=core.uid('artifact'),core.uid('obs'),core.uid('evidence')
    with core.connect() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',(aid,run,'traditional.tool.fixture',str(path),hashlib.sha256(path.read_bytes()).hexdigest(),'application/json',1,core.utcnow()))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,run,engagement['id'],'traditional','tool.observation','http://fixture.test/object','Recorded observation',.5,'fixture',aid,core.utcnow()))
        db.execute('INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)',(eid,oid,run,'negative_control','Recorded negative control',aid,'counterevidence',core.utcnow()))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(core.uid('obs'),run,engagement['id'],'traditional','unbacked','fixture','Unbacked legacy record',.2,'legacy',None,core.utcnow()))
    endpoint=f"/api/v1/research/engagements/{engagement['id']}/runs/{run}/campaign-source-plan"
    return engagement,campaign,run,path,aid,oid,endpoint


def create(client,project,run,preview):
    return client.post(f"/api/v1/engagements/{project['id']}/campaigns",json={'name':'Late evidence campaign','objective':'Review recorded inputs from this Run only','source_run_id':run,'source_plan_hash':preview['preview_hash']})


def test_reviewed_creation_projects_only_selected_run_and_preserves_counterevidence(client):
    project,old,run,path,aid,oid,url=fixture_materials(client)
    before=core.DB.read_bytes();preview=client.get(url);assert preview.status_code==200,preview.text
    assert core.DB.read_bytes()==before
    preview=preview.json();assert preview['counts']=={'artifacts':1,'observations':1,'evidence':0,'counterevidence':1,'excluded':1}
    assert str(path) not in str(preview) and preview['sensitivity']=='private' and preview['executes_tools'] is False
    result=create(client,project,run,preview);assert result.status_code==201,result.text
    value=result.json();assert value['source_import']['nodes']==3
    with core.connect() as db:
        nodes=db.execute('SELECT * FROM research_nodes WHERE campaign_id=?',(value['id'],)).fetchall()
        assert {r['node_type'] for r in nodes}=={'artifact','observation','counterevidence'}
        assert all(r['run_id']==run and core.load(r['attributes_json'])['sensitivity']=='private' for r in nodes)
        assert db.execute('SELECT count(*) FROM research_nodes WHERE campaign_id=?',(old['id'],)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM agent_tasks').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM research_edges WHERE campaign_id=?',(value['id'],)).fetchone()[0]==3


@pytest.mark.parametrize('mutation',['file','source','scope','synthetic'])
def test_stale_or_ineligible_materials_never_create_partial_campaign(client,mutation):
    project,old,run,path,aid,oid,url=fixture_materials(client);preview=client.get(url).json()
    with core.connect() as db:
        if mutation=='source':db.execute("UPDATE observations SET summary='changed' WHERE id=?",(oid,))
        if mutation=='scope':db.execute("UPDATE analysis_runs SET scope_snapshot_id='old-scope' WHERE id=?",(run,))
        if mutation=='synthetic':db.execute('UPDATE analysis_runs SET synthetic=1 WHERE id=?',(run,))
    if mutation=='file':path.write_text('changed')
    assert create(client,project,run,preview).status_code==409
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM research_campaigns').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==0


def test_post_import_source_change_rolls_back_entire_creation(client,monkeypatch):
    project,old,run,path,aid,oid,url=fixture_materials(client);preview=client.get(url).json();original=materials.project
    def mutate(db,campaign,value):
        result=original(db,campaign,value);path.write_text('source changed during import');return result
    monkeypatch.setattr(materials,'project',mutate)
    assert create(client,project,run,preview).status_code==409
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM research_campaigns').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM v5_events WHERE event_type='campaign.recorded_inputs_imported'").fetchone()[0]==0


def test_foreign_source_and_missing_review_are_rejected(client):
    project,old,run,path,aid,oid,url=fixture_materials(client)
    assert client.get(url.replace(project['id'],'foreign')).status_code==404
    response=client.post(f"/api/v1/engagements/{project['id']}/campaigns",json={'name':'Unchecked','objective':'An unchecked recorded source','source_run_id':run})
    assert response.status_code==422
    with core.connect() as db:db.execute("UPDATE observations SET raw_ref='another-run-artifact' WHERE id=?",(oid,))
    assert client.get(url).status_code==409


def test_storage_maintenance_blocks_read_and_atomic_projection(client):
    from storage_access import storage_access
    project,old,run,path,aid,oid,url=fixture_materials(client);preview=client.get(url).json()
    with storage_access(core.LOCAL_DATA_ROOT,maintenance=True):
        assert client.get(url).status_code==409
        assert create(client,project,run,preview).status_code==409
    with core.connect() as db:assert db.execute('SELECT count(*) FROM research_campaigns').fetchone()[0]==1


def test_in_place_authority_change_invalidates_preview(client):
    project,old,run,path,aid,oid,url=fixture_materials(client);preview=client.get(url).json()
    with core.connect() as db:db.execute("UPDATE scope_snapshots SET rules=rules || ' ' WHERE id=?",(project['current_scope_snapshot_id'],))
    assert create(client,project,run,preview).status_code==409


def test_bounded_import_never_silently_truncates_run_records(client):
    project,old,run,path,aid,oid,url=fixture_materials(client);preview=client.get(url).json()
    with core.connect() as db:
        for index in range(500):
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(f'bounded-{index}',run,project['id'],'traditional','tool.observation','fixture','Bounded source',.1,'fixture',aid,core.utcnow()))
    assert client.get(url).status_code==409
    assert create(client,project,run,preview).status_code==409
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM research_campaigns').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==502


def test_other_run_materials_are_never_projected(client):
    project,old,run,path,aid,oid,url=fixture_materials(client)
    with core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('other-run',project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'completed','report',0,None,None,None,None,core.utcnow()))
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',('other-observation','other-run',project['id'],'traditional','tool.observation','other-target','Other Run material',.5,'fixture',None,core.utcnow()))
    preview=client.get(url);assert preview.status_code==200 and 'other-observation' not in preview.text
    result=create(client,project,run,preview.json());assert result.status_code==201
    with core.connect() as db:assert db.execute("SELECT count(*) FROM research_nodes WHERE source_ref='other-observation'").fetchone()[0]==0


def test_registered_counterevidence_survives_unbacked_legacy_observation(client):
    project,old,run,path,aid,oid,url=fixture_materials(client)
    with core.connect() as db:
        legacy=db.execute('SELECT id FROM observations WHERE run_id=? AND raw_ref IS NULL',(run,)).fetchone()[0]
        db.execute('INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)',(core.uid('evidence'),legacy,run,'negative_control','Artifact-backed counterevidence',aid,'negative',core.utcnow()))
    preview=client.get(url).json();assert preview['counts']['counterevidence']==2
    result=create(client,project,run,preview);assert result.status_code==201,result.text
    with core.connect() as db:
        rows=db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND node_type='counterevidence'",(result.json()['id'],)).fetchall()
        assert len(rows)==2 and any(core.load(r['attributes_json'])['observation_linked'] is False for r in rows)
