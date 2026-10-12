import base64,hashlib,io,json,zipfile
import pytest
import final_core as core
import extension_packages as packages
from tests.test_final import client


def package(version='1',identity='fixture-pack',extra=None):
    stream=io.BytesIO()
    manifest={'id':identity,'name':'Fixture package','version':version,'type':'pack','entrypoint':'entry.py','capabilities':['network.request']}
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('fieldwork-extension.json',json.dumps(manifest))
        archive.writestr('entry.py','raise RuntimeError("Package code must never execute")')
        if extra:archive.writestr(*extra)
    payload=stream.getvalue()
    return {'package_base64':base64.b64encode(payload).decode(),'expected_sha256':hashlib.sha256(payload).hexdigest()}


def install(client,body,current=None):
    return client.post('/api/v1/workspace/extension-packages/install',json={**body,'confirmed':True,'expected_current_sha256':current})


def test_local_package_update_restore_integrity_and_no_execution(client):
    first=package();second=package('2')
    url='/api/v1/workspace/extension-packages'
    before=core.DB.read_bytes()
    preview=client.post(url+'/preview',json=first)
    assert preview.status_code==200 and preview.json()['current_sha256'] is None
    assert core.DB.read_bytes()==before
    assert install(client,first).status_code==200
    assert install(client,second).status_code==409
    assert install(client,second,first['expected_sha256']).status_code==200
    items=client.get(url).json()['items']
    assert items[0]['manifest']['version']=='2' and items[0]['integrity']=='verified'
    assert items[0]['audit_confirmed'] and not items[0]['enabled'] and not items[0]['execution_supported']
    versions=client.get(url+'/fixture-pack/versions').json()['items']
    assert len(versions)==2
    response=client.post(url+'/fixture-pack/restore',json={'sha256':first['expected_sha256'],'expected_current_sha256':second['expected_sha256'],'confirmed':True})
    assert response.status_code==200
    assert client.get(url).json()['items'][0]['manifest']['version']=='1'
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM capability_grants_v6').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
    (packages.root()/('archive-'+first['expected_sha256']+'.zip')).write_bytes(b'corrupt')
    item=client.get(url).json()['items'][0]
    assert item['integrity']=='failed' and item['state']=='storage_unconfirmed'
    assert client.post(url+'/fixture-pack/restore',json={'sha256':first['expected_sha256'],'expected_current_sha256':first['expected_sha256'],'confirmed':True}).status_code==409


@pytest.mark.parametrize('extra',[('../escape.py','unsafe'),('/absolute.py','unsafe'),('entry.py','duplicate'),('large.bin',b'a'*(packages.MAX_BYTES+1))])
def test_package_rejects_paths_duplicates_and_expansion(client,extra):
    body=package(extra=extra)
    assert client.post('/api/v1/workspace/extension-packages/preview',json=body).status_code==422
    assert client.get('/api/v1/workspace/extension-packages').json()['items']==[]


def test_package_requires_digest_confirmation_and_compatible_identity(client):
    body=package()
    assert client.post('/api/v1/workspace/extension-packages/preview',json={**body,'expected_sha256':'a'*64}).status_code==422
    assert client.post('/api/v1/workspace/extension-packages/install',json={**body,'confirmed':False}).status_code==422
    assert client.post('/api/v1/workspace/extension-packages/install',json={**body,'confirmed':1}).status_code==422
    assert client.post('/api/v1/workspace/extension-packages/install',json={**body,'confirmed':True,'execute':True}).status_code==422
    assert install(client,body).status_code==200
    other=package(identity='other-pack');assert install(client,other).status_code==200
    assert client.post('/api/v1/workspace/extension-packages/fixture-pack/restore',json={'sha256':other['expected_sha256'],'expected_current_sha256':body['expected_sha256'],'confirmed':True}).status_code==409


def test_package_storage_refuses_link_and_missing_audit(client,tmp_path):
    directory=packages.root();directory.rmdir()
    directory.symlink_to(tmp_path)
    assert client.get('/api/v1/workspace/extension-packages').status_code==409
    directory.unlink();directory.mkdir(mode=0o700)
    body=package();assert install(client,body).status_code==200
    head=packages.read('package-fixture-pack.json');head['event_id']=-1
    packages.write('package-fixture-pack.json',head)
    item=client.get('/api/v1/workspace/extension-packages').json()['items'][0]
    assert not item['audit_confirmed'] and item['state']=='storage_unconfirmed'


def test_package_history_pages_and_failed_commit_restores_head(client,monkeypatch):
    from contextlib import contextmanager
    first=package();second=package('2')
    assert install(client,first).status_code==200
    original=core.connect
    @contextmanager
    def failed_commit():
        with original() as db:
            yield db
            raise OSError('Simulated database commit failure')
    monkeypatch.setattr(core,'connect',failed_commit)
    assert install(client,second,first['expected_sha256']).status_code==409
    monkeypatch.setattr(core,'connect',original)
    assert client.get('/api/v1/workspace/extension-packages').json()['items'][0]['sha256']==first['expected_sha256']
    assert install(client,second,first['expected_sha256']).status_code==200
    page=client.get('/api/v1/workspace/extension-packages/fixture-pack/versions',params={'limit':1}).json()
    assert len(page['items'])==1 and page['has_more']
    tail=client.get('/api/v1/workspace/extension-packages/fixture-pack/versions',params={'limit':1,'offset':1}).json()
    assert len(tail['items'])==1 and not tail['has_more']
    assert page['items'][0]['event_id']!=tail['items'][0]['event_id']


def test_package_symlink_member_and_duplicate_manifest_keys_are_rejected(client):
    for malicious in ('symlink','duplicate-key'):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            if malicious=='duplicate-key':
                archive.writestr('fieldwork-extension.json','{"id":"one","id":"two"}')
                archive.writestr('entry.py','pass')
            else:
                archive.writestr('fieldwork-extension.json',json.dumps({'id':'fixture','name':'Fixture','version':'1','type':'pack','entrypoint':'entry.py','capabilities':[]}))
                info=zipfile.ZipInfo('entry.py');info.external_attr=0o120777<<16
                archive.writestr(info,'/outside')
        payload=stream.getvalue()
        response=client.post('/api/v1/workspace/extension-packages/preview',json={'package_base64':base64.b64encode(payload).decode(),'expected_sha256':hashlib.sha256(payload).hexdigest()})
        assert response.status_code==422


def test_evidence_archive_captures_package_versions_and_pointer(client,tmp_path,monkeypatch):
    from tests.test_v5_workspace_ui import _backup_artifact
    import evidence_backups
    _backup_artifact(client,tmp_path,monkeypatch)
    first=package();second=package('2')
    assert install(client,first).status_code==200
    assert install(client,second,first['expected_sha256']).status_code==200
    response=client.post('/api/v1/workspace/evidence-backups',json={})
    assert response.status_code==201,response.text
    backup=response.json()
    assert backup['extension_file_count']==3
    before=core.DB.read_bytes();head=packages.read('package-fixture-pack.json')
    result=client.post('/api/v1/workspace/evidence-backups/'+backup['id']+'/restore-drill',json={})
    assert result.status_code==200,result.text
    assert result.json()['extension_file_count']==3 and not result.json()['live_extensions_replaced']
    assert core.DB.read_bytes()==before and packages.read('package-fixture-pack.json')==head
    # Missing current version cannot be silently omitted from a new backup.
    (packages.root()/('archive-'+second['expected_sha256']+'.zip')).unlink()
    assert client.post('/api/v1/workspace/evidence-backups',json={}).status_code==409


def test_evidence_archive_refuses_busy_package_writer(client,tmp_path,monkeypatch):
    import fcntl,os
    from tests.test_v5_workspace_ui import _backup_artifact
    _backup_artifact(client,tmp_path,monkeypatch)
    body=package();assert install(client,body).status_code==200
    with (packages.root()/'control.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert client.post('/api/v1/workspace/evidence-backups',json={}).status_code==409


def test_legacy_format_one_evidence_archive_still_drills(client,tmp_path,monkeypatch):
    from tests.test_v5_workspace_ui import _backup_artifact
    _backup_artifact(client,tmp_path,monkeypatch)
    response=client.post('/api/v1/workspace/evidence-backups',json={})
    backup=response.json();path=tmp_path/'backups'/(backup['id']+'.zip')
    with zipfile.ZipFile(path) as archive:entries={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(entries['manifest.json']);manifest['format']=1;manifest.pop('extension_files')
    entries['manifest.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(path,'w') as archive:
        for name,payload in entries.items():archive.writestr(name,payload)
    metadata_path=tmp_path/'backups'/(backup['id']+'.evidence.json')
    metadata=json.loads(metadata_path.read_text());metadata.pop('extension_file_count');metadata['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata))
    response=client.post('/api/v1/workspace/evidence-backups/'+backup['id']+'/restore-drill',json={})
    assert response.status_code==200,response.text
    assert response.json()['extension_file_count']==0 and response.json()['integrity_ok']


@pytest.mark.parametrize('prefix,count',[('package-',129),('archive-',501)])
def test_package_backup_rejects_inventory_overflow_before_copying(client,prefix,count):
    directory=packages.root()
    suffix='.json' if prefix=='package-' else '.zip'
    for i in range(count):(directory/(prefix+str(i)+suffix)).write_bytes(b'fixture')
    with pytest.raises(ValueError,match='Package backup file limit'):
        with packages.snapshot_files(core.LOCAL_DATA_ROOT):raise AssertionError('Overflow must not yield a snapshot')
