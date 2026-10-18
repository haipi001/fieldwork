from types import SimpleNamespace
import pytest
import final_core as core
import agent_monitor_windows as windows
import monitor_alerts as alerts
import monitor_window_replay as replay
from tests.test_agent_audit import client,fixture
from tests.test_monitor_alerts import setup_monitor


def prepare(client,fixture):
    aid=setup_monitor(client,fixture);alerts.configure(True,None);windows.tick(aid)
    item=alerts.inbox()['items'][0]
    return item,'/api/v1/workspace/monitor-alerts/'+item['id']+'/reconstruction'


def test_real_separate_process_receipt_persists_and_is_idempotent(client,fixture,monkeypatch):
    item,url=prepare(client,fixture)
    assert client.get(url).json()=={'receipt':None}
    body={'review_hash':item['review_hash'],'confirmed_policy_only':True}
    assert client.post(url,json={**body,'confirmed_policy_only':False}).status_code==422
    assert client.post(url,json={**body,'command':'arbitrary'}).status_code==422
    assert client.post(url,json={**body,'review_hash':'0'*64}).status_code==409
    result=client.post(url,json=body);assert result.status_code==200,result.text
    value=result.json();assert value['status']=='reproduced' and value['signal_count']>0
    assert value['verification_status']=='human_review' and value['behavior_authenticity_verified'] is False
    assert value['canonical_finding_created'] is False
    import os
    assert value['executor_pid']!=os.getpid()
    assert set(value['implementation_sha256'])=={'monitor_window_replay_worker.py','agent_audit.py'}
    assert client.get(url).json()['receipt']==value
    def unexpected(*args,**kwargs):raise AssertionError('Repeated reconstruction must reuse receipt')
    monkeypatch.setattr(replay.subprocess,'run',unexpected)
    assert client.post(url,json=body).json()==value
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM v5_events WHERE event_type='monitor.window_reconstructed'").fetchone()[0]==1


def test_failed_worker_and_changed_source_store_no_receipt(client,fixture,monkeypatch):
    item,url=prepare(client,fixture)
    body={'review_hash':item['review_hash'],'confirmed_policy_only':True}
    original=replay.subprocess.run
    monkeypatch.setattr(replay.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b''))
    assert client.post(url,json=body).status_code==409
    assert client.get(url).json()=={'receipt':None}
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        alerts.acknowledge(item['id'],item['review_hash'])
        return result
    monkeypatch.setattr(replay.subprocess,'run',changed)
    assert client.post(url,json=body).status_code==409
    assert client.get(url).json()=={'receipt':None}


def test_worker_detects_policy_result_mismatch_without_promoting(client,fixture):
    from monitor_window_replay_worker import reconstruct
    item,url=prepare(client,fixture)
    with core.connect() as db:_,_,window=replay._window(db,item['id'])
    window['policy_evaluations']={}
    result=reconstruct(window)
    assert result['status']=='mismatch' and result['verification_status']=='human_review'
    window['input_digest']='0'*64
    with pytest.raises(ValueError):reconstruct(window)


def test_storage_maintenance_blocks_replay_and_timeout_has_no_receipt(client,fixture,monkeypatch):
    from storage_access import storage_access
    item,url=prepare(client,fixture)
    body={'review_hash':item['review_hash'],'confirmed_policy_only':True}
    with storage_access(core.LOCAL_DATA_ROOT,maintenance=True):
        assert client.post(url,json=body).status_code==409
    def timeout(*a,**kw):raise replay.subprocess.TimeoutExpired('fixed-worker',15)
    monkeypatch.setattr(replay.subprocess,'run',timeout)
    assert client.post(url,json=body).status_code==409
    assert client.get(url).json()=={'receipt':None}
