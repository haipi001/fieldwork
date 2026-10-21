import hashlib
import sqlite3
import pytest
import final_core as core
from tests.test_final import client
from tests.test_agent_attack_adapters import setup as adapter_setup,register,SOURCE


def two_versions(client):
    agent,url=adapter_setup(client)
    first=register(client,url,SOURCE,True).json()
    second=register(client,url,SOURCE+'# second version\n',True,first['configuration_sha256']).json()
    return agent,url,first,second


def restore_preview(client,url,source,config):
    body={'source_sha256':source,'expected_configuration_sha256':config}
    value=client.post(url+'/restore-preview',json=body)
    assert value.status_code==200,value.text
    return body|{'preview_hash':value.json()['preview_hash'],'confirmed':True}


def test_archive_restore_is_disabled_private_idempotent_per_source_and_never_executes(client,monkeypatch):
    import agent_attack_executor
    monkeypatch.setattr(agent_attack_executor,'execute_local_adapter',lambda *_a,**_k:pytest.fail('Version maintenance must not execute'))
    agent,url,first,second=two_versions(client)
    versions=client.get(url+'/versions');assert versions.status_code==200,versions.text
    entries=versions.json()['items'];assert len(entries)==2 and all(x['archived'] for x in entries)
    assert 'source_code' not in versions.text and SOURCE not in versions.text
    source=client.get(url+'/versions/'+first['source_sha256']+'/source')
    assert source.status_code==200 and source.json()['source_code']==SOURCE
    assert hashlib.sha256(source.json()['source_code'].encode()).hexdigest()==first['source_sha256']
    with core.connect() as db:
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone()
        component=client.get('/api/v1/workspace/components/'+row['id'])
        assert component.status_code==200 and 'source_code' not in component.text and SOURCE not in component.text
    body=restore_preview(client,url,first['source_sha256'],second['configuration_sha256'])
    assert client.put(url+'/restore',json=body|{'confirmed':False}).status_code==422
    assert client.put(url+'/restore',json=body|{'enabled':True}).status_code==422
    restored=client.put(url+'/restore',json=body);assert restored.status_code==200,restored.text
    assert restored.json()['enabled'] is False and restored.json()['remains_disabled'] is True
    assert restored.json()['source_sha256']==first['source_sha256']
    assert client.put(url+'/restore',json=body).status_code==409
    later=register(client,url,SOURCE,False,restored.json()['configuration_sha256'])
    assert later.status_code==200 and later.json()['stored_source_versions']==2
    assert len(client.get(url+'/versions').json()['items'])==2
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM agent_supply_components_v5').fetchone()[0]==1
        for table in ('agent_tasks','runtime_calls','analysis_runs','agent_attack_tests_v5','capability_grants_v6'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        assert all('source_code' not in row[0] and SOURCE not in row[0] for row in db.execute('SELECT payload_json FROM v5_events'))


def test_legacy_current_source_is_not_invented_history_and_is_captured_before_replacement(client):
    agent,url=adapter_setup(client);value=register(client,url).json()
    with core.connect() as db:
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone()
        metadata=core.load(row['metadata_json']);metadata.pop('source_history');metadata.pop('history_sha256')
        db.execute('UPDATE agent_supply_components_v5 SET metadata_json=? WHERE id=?',(core.dump(metadata),row['id']))
    current=client.get(url).json()
    versions=client.get(url+'/versions').json()
    assert len(versions['items'])==1 and versions['items'][0]['archived'] is False
    assert versions['items'][0]['recorded_at'] is None and versions['stored_source_versions']==0
    assert client.get(url).json()['configuration_sha256']==current['configuration_sha256']
    replacement=register(client,url,SOURCE+'# replacement\n',False,current['configuration_sha256'])
    assert replacement.status_code==200,replacement.text
    stored=client.get(url+'/versions').json()['items']
    old=next(x for x in stored if x['source_sha256']==value['source_sha256'])
    assert old['archived'] and old['origin']=='captured_existing_current'
    assert client.get(url+'/versions/'+value['source_sha256']+'/source').json()['source_code']==SOURCE
    assert client.get(url+'/versions/'+'0'*64+'/source').status_code==404


@pytest.mark.parametrize('tamper',['code','record','missing_index'])
def test_archive_tampering_never_silently_discards_or_restores_source(client,tamper):
    agent,url,first,second=two_versions(client)
    body=restore_preview(client,url,first['source_sha256'],second['configuration_sha256'])
    with core.connect() as db:
        row=db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone()
        metadata=core.load(row['metadata_json'])
        if tamper=='code':metadata['source_history'][0]['source_code']+='changed'
        elif tamper=='record':metadata['source_history'][0]['recorded_at']='changed'
        else:metadata.pop('source_history')
        db.execute('UPDATE agent_supply_components_v5 SET metadata_json=? WHERE id=?',(core.dump(metadata),row['id']))
    assert client.get(url+'/versions').status_code==409
    assert client.put(url+'/restore',json=body).status_code==409


def test_history_bound_stale_restore_and_failed_write_preserve_active_version(client,monkeypatch):
    import agent_adapter_history as history
    monkeypatch.setattr(history,'LIMIT',2)
    agent,url,first,second=two_versions(client)
    request={'source_code':SOURCE+'# third\n','expected_configuration_sha256':second['configuration_sha256'],'enabled':False}
    assert client.post(url+'/preview',json=request).status_code==409
    body=restore_preview(client,url,first['source_sha256'],second['configuration_sha256'])
    with core.connect() as db:
        original=dict(db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone())
        db.execute("CREATE TRIGGER reject_restore_event BEFORE INSERT ON v5_events WHEN NEW.event_type='agent_attack.adapter_restored_disabled' BEGIN SELECT RAISE(ABORT,'fixture event failure'); END")
    with pytest.raises(sqlite3.IntegrityError):client.put(url+'/restore',json=body)
    with core.connect() as db:
        assert dict(db.execute('SELECT * FROM agent_supply_components_v5 WHERE agent_id=?',(agent,)).fetchone())==original
        db.execute('DROP TRIGGER reject_restore_event')
    disabled=client.put(url+'/state',json={'expected_configuration_sha256':second['configuration_sha256'],'enabled':False,'confirmed':True})
    assert disabled.status_code==200
    assert client.put(url+'/restore',json=body).status_code==409
    entries=client.get(url+'/versions').json()['items'];assert len(entries)==2


def test_restoring_identical_old_source_does_not_revive_execution_approval(client):
    from tests.test_agent_attack_dispatch import setup as dispatch_setup
    audit,agent,request_url,inputs,plan=dispatch_setup(client)
    url='/api/v1/workspace/agent-attack-requests/adapters/'+agent
    first=client.get(url).json()
    second=register(client,url,SOURCE+'# replacement\n',True,first['configuration_sha256']).json()
    approved=restore_preview(client,url,first['source_sha256'],second['configuration_sha256'])
    assert client.put(url+'/restore',json=approved).status_code==200
    with core.connect() as db:
        before=db.execute('SELECT count(*) FROM analysis_runs').fetchone()[0]
    rejected=client.post(request_url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert rejected.status_code==409,rejected.text
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM analysis_runs').fetchone()[0]==before
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
