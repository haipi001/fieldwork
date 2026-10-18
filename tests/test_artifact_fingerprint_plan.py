import json
import pytest
import final_core
import v5_intelligence as intel
from tests.test_final import client
from tests.test_v5_intelligence import campaign,import_osv,_trivy_inventory_artifact


def setup(client):
    engagement,cid=campaign(client)
    import_osv(client)
    artifact,node=_trivy_inventory_artifact(client,engagement,cid)
    return cid,artifact


def test_artifact_fingerprint_preview_no_write_commit_uses_source_and_is_idempotent(client):
    cid,artifact=setup(client)
    base=f'/api/v1/intelligence/campaigns/{cid}/artifacts/{artifact}'
    listing=client.get(f'/api/v1/intelligence/campaigns/{cid}/package-artifacts').json()
    assert [x['id'] for x in listing['items']]==[artifact]
    assert 'uri' not in listing['items'][0]
    preview=client.get(base+'/fingerprint-plan')
    assert preview.status_code==200,preview.text
    value=preview.json();assert value['packages']==[{'ecosystem':'PyPI','name':'example-pkg','version':'1.2.3','source_kind':'artifact','source_ref':artifact}]
    assert value['inventory_mode']=='vulnerability_records_only'
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_matches').fetchone()[0]==0
    first=client.post(base+'/fingerprint',json={'preview_hash':value['preview_hash']})
    assert first.status_code==200,first.text
    assert len(first.json()['matches'])==1
    second=client.post(base+'/fingerprint',json={'preview_hash':value['preview_hash']})
    assert second.status_code==200 and second.json()['matches'][0]['id']==first.json()['matches'][0]['id']
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM intel_matches').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM verification_requests_v5').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0


@pytest.mark.parametrize('case',['stale_preview','foreign_artifact','changed_bytes'])
def test_fingerprint_source_boundary(client,case):
    cid,artifact=setup(client);base=f'/api/v1/intelligence/campaigns/{cid}/artifacts/{artifact}'
    plan=client.get(base+'/fingerprint-plan').json()
    if case=='foreign_artifact':
        _,other=campaign(client,'https://foreign-package.example.test')
        assert client.get(f'/api/v1/intelligence/campaigns/{other}/artifacts/{artifact}/fingerprint-plan').status_code==409
        return
    if case=='changed_bytes':
        from pathlib import Path
        with final_core.connect() as db:path=db.execute('SELECT uri FROM artifacts WHERE id=?',(artifact,)).fetchone()[0]
        Path(path).write_text('{}')
    result=client.post(base+'/fingerprint',json={'preview_hash':'0'*64 if case=='stale_preview' else plan['preview_hash']})
    assert result.status_code==409
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_matches').fetchone()[0]==0


def test_fingerprint_postwrite_source_recheck_rolls_back_all_nodes_and_matches(client,monkeypatch):
    cid,artifact=setup(client);base=f'/api/v1/intelligence/campaigns/{cid}/artifacts/{artifact}'
    plan=client.get(base+'/fingerprint-plan').json()
    with final_core.connect() as db:before=db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]
    original=intel._artifact_fingerprint_plan;calls=0
    def changed(*args):
        nonlocal calls
        calls+=1;value=original(*args)
        return {**value,'preview_hash':'0'*64} if calls==2 else value
    monkeypatch.setattr(intel,'_artifact_fingerprint_plan',changed)
    response=client.post(base+'/fingerprint',json={'preview_hash':plan['preview_hash']})
    assert response.status_code==409
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM intel_matches').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==before


@pytest.mark.parametrize('case',['missing_purl','name_mismatch','oversized_version','unsupported_only','incomplete','bad_type','too_many'])
def test_auto_fingerprint_does_not_trust_invalid_or_unbounded_package_records(client,case):
    import hashlib
    from pathlib import Path
    cid,artifact=setup(client)
    with final_core.connect() as db:
        path=Path(db.execute('SELECT uri FROM artifacts WHERE id=?',(artifact,)).fetchone()[0])
        source=json.loads(path.read_text())
        item=next(x for x in source['observations'] if x['observation_type']=='code.dependency_vulnerability')
        if case=='missing_purl':item['structured']['PkgIdentifier']={}
        elif case=='name_mismatch':item['structured']['PkgName']='foreign'
        elif case=='oversized_version':
            item['structured']['InstalledVersion']='1'*201;item['structured']['PkgIdentifier']['PURL']='pkg:pypi/example-pkg@'+'1'*201
        elif case=='unsupported_only':item['structured']['PkgIdentifier']['PURL']='pkg:unknown/example-pkg@1.2.3'
        elif case=='incomplete':source['observations'].append({'observation_type':'code.dependency_inventory_incomplete'})
        elif case=='bad_type':item['observation_type']=[]
        else:
            source['observations']=[{'observation_type':'code.dependency_inventory','structured':{'PkgName':'example-pkg','InstalledVersion':str(i),'PkgIdentifier':{'PURL':f'pkg:pypi/example-pkg@{i}'}}} for i in range(101)]
        path.write_text(json.dumps(source));db.execute('UPDATE artifacts SET sha256=? WHERE id=?',(hashlib.sha256(path.read_bytes()).hexdigest(),artifact))
    result=client.get(f'/api/v1/intelligence/campaigns/{cid}/artifacts/{artifact}/fingerprint-plan')
    assert result.status_code==409,result.text
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_matches').fetchone()[0]==0
