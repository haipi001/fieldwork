from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import final_core as core
import agent_monitor_windows as windows
import monitor_alerts as alerts
from tests.test_agent_audit import client, fixture, create, upload


def setup_monitor(client, fixture):
    aid=create(client,fixture)
    for body in fixture['imports']:upload(client,aid,body)
    with core.connect() as db:
        db.execute('INSERT INTO agent_monitors(audit_id,status,started_at,last_scan_at) VALUES(?,?,?,?)',(aid,'active',core.utcnow(),core.utcnow()))
    return aid


def test_local_alert_dispatch_atomic_once_and_acknowledged(client,fixture):
    aid=setup_monitor(client,fixture)
    url='/api/v1/workspace/monitor-alerts'
    initial=client.get(url+'/configuration').json();assert not initial['enabled']
    body={'enabled':True,'expected_revision':initial['revision'],'confirmed_local_only':True}
    assert client.post(url+'/configuration',json={**body,'confirmed_local_only':False}).status_code==422
    assert client.post(url+'/configuration',json={**body,'command':'extra'}).status_code==422
    config=client.post(url+'/configuration',json=body);assert config.status_code==200
    assert client.post(url+'/configuration',json=body).status_code==409
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:windows.tick(aid,limit=1),range(30)))
    inbox=client.get(url).json();assert inbox['items']
    assert len({i['id'] for i in inbox['items']})==len(inbox['items'])
    assert all(i['verification_status']=='not_verified' and not i['acknowledged_at'] for i in inbox['items'])
    first=inbox['items'][0];endpoint=url+'/'+first['id']+'/acknowledge'
    assert client.post(endpoint,json={'review_hash':'0'*64}).status_code==409
    ack=client.post(endpoint,json={'review_hash':first['review_hash']});assert ack.status_code==200 and ack.json()['acknowledged_at']
    again=client.post(endpoint,json={'review_hash':ack.json()['review_hash']});assert again.json()==ack.json()
    assert client.post(endpoint,json={'review_hash':first['review_hash']}).status_code==409
    assert windows.tick(aid) is None
    assert len(client.get(url).json()['items'])==len(inbox['items'])
    assert client.get(url+'?limit=1').json()['has_more']==(len(inbox['items'])>1)
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM v5_events WHERE event_type='monitor.alert_acknowledged'").fetchone()[0]==1


def test_disabled_windows_not_backfilled_and_tampered_sources_fail(client,fixture):
    aid=setup_monitor(client,fixture)
    while windows.tick(aid):pass
    assert alerts.inbox()['items']==[]
    alerts.configure(True,None)
    assert alerts.inbox()['items']==[]
    # New monitor has distinct future windows; disabling preserves their history.
    second=setup_monitor(client,fixture);windows.tick(second)
    items=alerts.inbox()['items'];assert items
    alerts.configure(False,alerts.configuration()['revision'])
    assert alerts.inbox()['items']==items
    with core.connect() as db:
        row=db.execute('SELECT uri FROM artifacts WHERE id=?',(items[0]['artifact_id'],)).fetchone()
    Path(row['uri']).write_text('{}')
    response=client.get('/api/v1/workspace/monitor-alerts');assert response.status_code==409


def test_dispatch_failure_rolls_back_window_and_event_consumption(client,fixture,monkeypatch):
    import pytest
    aid=setup_monitor(client,fixture);alerts.configure(True,None)
    original=alerts._write
    def fail(db,entity,kind,payload):
        if kind=='monitor.alert_created':raise OSError('isolated delivery failure')
        return original(db,entity,kind,payload)
    monkeypatch.setattr(alerts,'_write',fail)
    with pytest.raises(OSError):windows.tick(aid)
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_monitor_windows').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM agent_monitor_window_events').fetchone()[0]==0
    monkeypatch.setattr(alerts,'_write',original)
    assert windows.tick(aid)
    assert alerts.inbox()['items']


def test_alert_source_uses_frozen_window_not_later_policy(client,fixture):
    import agent_audit as audit
    import json
    aid=client.post('/api/v1/agent-audit/monitor/start').json()['id']
    denied=audit.Policy().model_dump()
    policy=client.post(f'/api/v1/agent-audit/monitor/{aid}/policy',json={'policy':denied,'expected_id':None,'confirmed':True}).json()
    upload(client,aid,{'content':json.dumps({'events':[{'timestamp':core.utcnow(),'source_type':'network','action_type':'connect','network_host':'example.test','resource':'https://example.test/?api_key=secret-source-token','status':'success'}]})})
    alerts.configure(True,None);windows.tick(aid)
    item=alerts.inbox()['items'][0]
    with core.connect() as db:
        before=db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0]
    endpoint='/api/v1/workspace/monitor-alerts/'+item['id']+'/source'
    source=client.get(endpoint);assert source.status_code==200,source.text
    first=source.json()
    assert first['alert']['id']==item['id'] and first['frozen_input_digest']==item['input_digest']
    assert first['events'] and all(e['evaluation']['decision']=='violation' for e in first['events'])
    assert first['events'][0]['monitor_policy_id']==policy['id']
    assert first['view']=='redacted_frozen_window' and first['verification_status']=='not_verified'
    assert 'secret-source-token' not in source.text
    with core.connect() as db:assert db.execute('SELECT COUNT(*) FROM v5_events').fetchone()[0]==before
    allowed={**denied,'internet_access':True,'allowed_network_hosts':['example.test']}
    response=client.post(f'/api/v1/agent-audit/monitor/{aid}/policy',json={'policy':allowed,'expected_id':policy['id'],'confirmed':True});assert response.status_code==200
    assert client.get(endpoint).json()==first
    assert client.get('/api/v1/workspace/monitor-alerts/missing/source').status_code==404
    with core.connect() as db:
        row=db.execute('SELECT uri FROM artifacts WHERE id=?',(item['artifact_id'],)).fetchone()
    Path(row['uri']).write_text('{}')
    assert client.get(endpoint).status_code==409


def test_native_feed_baseline_dedup_ack_and_no_sensitive_content(client,fixture):
    alerts.configure(True,None)
    endpoint='/api/v1/workspace/monitor-alert-native-delivery'
    baseline=client.get(endpoint).json();assert baseline['cursor']==0 and baseline['items']==[]
    aid=setup_monitor(client,fixture);windows.tick(aid)
    initial=client.get(endpoint).json();assert initial['cursor']>0 and initial['items']==[]
    feed=client.get(endpoint,params={'after':0}).json();assert feed['items']
    assert set(feed['items'][0])=={'id','signal_count'}
    assert 'resource' not in str(feed) and 'artifact_id' not in str(feed)
    assert client.get(endpoint,params={'after':feed['cursor']}).json()['items']==[]
    item=alerts.inbox()['items'][0];alerts.acknowledge(item['id'],item['review_hash'])
    assert client.get(endpoint,params={'after':0}).json()['items']==[]
    assert client.get(endpoint,params={'after':feed['cursor']+100}).json()['items']==[]
    alerts.configure(False,alerts.configuration()['revision'])
    assert client.get(endpoint,params={'after':0}).json()['items']==[]
    assert client.get(endpoint,params={'after':-1}).status_code==422
