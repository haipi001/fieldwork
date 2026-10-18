import pytest
import final_core as f
from tests.test_final import client, create_ready


def test_permanent_reset_removes_all_record_tables_and_backups(client):
    project=create_ready(client)
    for name in ('artifacts','exports','web3-imports','backups','restore-drills'):
        directory=f.LOCAL_DATA_ROOT/name
        directory.mkdir(exist_ok=True)
        (directory/'record.txt').write_text('private research fixture')
    with f.connect() as db:
        db.execute("INSERT INTO runtime_providers(id,kind,name,enabled,metadata_json,created_at,updated_at) VALUES('kept','local','Keep configuration',1,'{}','now','now')")
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='deleted'
    assert client.get('/api/v1/engagements?mode=traditional').json()==[]
    import research_history as h
    with f.connect() as db:
        assert not any(h.inventory(db).values())
        assert db.execute("SELECT name FROM runtime_providers WHERE id='kept'").fetchone()[0]=='Keep configuration'
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='runtime_calls_no_delete'").fetchone()[0]==1
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for name in ('artifacts','exports','web3-imports','backups','restore-drills'):
        assert not (f.LOCAL_DATA_ROOT/name).exists()
    assert create_ready(client)['id']!=project['id']


def test_reset_requires_explicit_phrase_and_keeps_data_on_block(client):
    project=create_ready(client)
    assert client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'CLEAR_RECENT_RECORDS'}).status_code==422
    with f.connect() as db:
        db.execute("INSERT INTO analysis_runs(id,engagement_id,mode,scope_snapshot_id,policy_id,status,current_stage,created_at) VALUES('blocked',?,'traditional',?,?,'running','target','now')",(project['id'],project['current_scope_snapshot_id'],project['current_policy_id']))
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==409,response.text
    assert client.get('/api/v1/engagements/'+project['id']).status_code==200


def test_file_stage_failure_rolls_back_records_and_restores_directories(client,monkeypatch):
    from pathlib import Path
    project=create_ready(client)
    for name in ('artifacts','exports'):
        (f.LOCAL_DATA_ROOT/name).mkdir()
        (f.LOCAL_DATA_ROOT/name/'file').write_text('keep on failure')
    original=Path.rename
    def fail_exports(path,destination):
        if path.name=='exports':raise OSError('fixture move failure')
        return original(path,destination)
    monkeypatch.setattr(Path,'rename',fail_exports)
    with pytest.raises(OSError,match='fixture move failure'):
        client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert client.get('/api/v1/engagements/'+project['id']).status_code==200
    assert (f.LOCAL_DATA_ROOT/'artifacts'/'file').read_text()=='keep on failure'
    assert (f.LOCAL_DATA_ROOT/'exports'/'file').is_file()


def test_filesystem_delete_failure_never_reports_success_and_can_retry(client,monkeypatch):
    import research_history as h
    create_ready(client)
    (f.LOCAL_DATA_ROOT/'artifacts').mkdir()
    (f.LOCAL_DATA_ROOT/'artifacts'/'file').write_text('pending removal')
    original=h.shutil.rmtree
    def fail(path,*args,**kwargs):
        if path.name.startswith('.research-purge-'):raise OSError('fixture delete failure')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(h.shutil,'rmtree',fail)
        response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
        assert response.status_code==500
        assert list(f.LOCAL_DATA_ROOT.glob('.research-purge-*'))
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==200,response.text
    assert not list(f.LOCAL_DATA_ROOT.glob('.research-purge-*'))


def test_owned_session_cleanup_is_retried_after_failure_without_deleting_provider_secrets(client,monkeypatch):
    import session_capture
    project=create_ready(client)
    with f.connect() as db:
        db.execute('INSERT INTO identities VALUES(?,?,?,?,?,?,?)',('session-test',project['id'],'test','user',None,'keychain://fieldwork-session/session-test','now'))
        db.execute('INSERT INTO identities VALUES(?,?,?,?,?,?,?)',('external-test',project['id'],'external','user',None,'keychain://another-service/external-test','now'))
    removed=[]
    def fail(identity):
        removed.append(identity)
        raise RuntimeError('fixture keychain unavailable')
    monkeypatch.setattr(session_capture,'delete_keychain',fail)
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==500
    assert removed==['session-test']
    monkeypatch.setattr(session_capture,'delete_keychain',removed.append)
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==200,response.text
    assert removed==['session-test','session-test']
    assert not list(f.LOCAL_DATA_ROOT.glob('.research-purge-*'))


def test_purge_rejects_concurrent_mutations_but_normal_controls_stay_concurrent(client,monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event()
    def hold(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return {'fixture':True}
    monkeypatch.setattr(f,'onboarding_checks',hold)
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting=executor.submit(client.post,'/api/v1/onboarding/self-test')
        try:
            assert entered.wait(3)
            assert client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'}).status_code==409
            start=time.monotonic()
            response=client.post('/api/v1/targets/resolve',json={'target':'https://example.test','mode':'traditional'})
            assert response.status_code==200,response.text
            assert time.monotonic()-start<1
        finally:release.set()
        assert waiting.result().status_code==200


def test_new_mutations_are_blocked_during_permanent_file_cleanup(client,monkeypatch):
    import research_history as h
    import threading
    from concurrent.futures import ThreadPoolExecutor
    entered=threading.Event();release=threading.Event();original=h.remove_staged_history
    def hold(directory):
        entered.set()
        assert release.wait(5)
        return original(directory)
    monkeypatch.setattr(h,'remove_staged_history',hold)
    with ThreadPoolExecutor(max_workers=1) as executor:
        clearing=executor.submit(client.request,'DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
        try:
            assert entered.wait(3)
            response=client.post('/api/v1/targets/resolve',json={'target':'https://example.test','mode':'traditional'})
            assert response.status_code==409,response.text
        finally:release.set()
        assert clearing.result().status_code==200
    assert client.post('/api/v1/targets/resolve',json={'target':'https://example.test','mode':'traditional'}).status_code==200


def test_unstarted_queue_is_included_in_confirmed_full_delete(client):
    from tests.test_v5_workspace_ui import team
    engagement,campaign,run,value=team(client)
    with f.connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed' WHERE id=?",(run,))
    plan=client.get('/api/v1/maintenance/all-research').json()
    assert plan['pending_tasks']==2
    assert plan['blockers']==[]
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==200,response.text
    with f.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_tasks').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM engagements_v2').fetchone()[0]==0


def test_leased_tasks_and_live_executor_still_block_delete(client,monkeypatch):
    from tests.test_v5_workspace_ui import team
    from types import SimpleNamespace
    import v5_research_worker as worker
    engagement,campaign,run,value=team(client)
    with f.connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed' WHERE id=?",(run,))
        db.execute("UPDATE agent_tasks SET status='leased',lease_owner='fixture-worker' WHERE id=?",(value['tasks'][0]['id'],))
    response=client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'})
    assert response.status_code==409,response.text
    assert 'agent_tasks' in response.json()['detail']['blockers']
    with f.connect() as db:
        db.execute("UPDATE agent_tasks SET status='queued',lease_owner=NULL")
    with monkeypatch.context() as patch:
        patch.setattr(worker,'_jobs',{'fixture':SimpleNamespace(is_alive=lambda:True)})
        assert 'research_executor' in client.get('/api/v1/maintenance/all-research').json()['blockers']
        assert client.request('DELETE','/api/v1/maintenance/all-research',json={'confirmation':'DELETE_ALL_RESEARCH'}).status_code==409
    assert client.get('/api/v1/engagements/'+engagement['id']).status_code==200
