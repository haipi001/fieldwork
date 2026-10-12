import time
import json
import subprocess
import sys
import os
from types import SimpleNamespace
import pytest
import final_core as core
import monitor_process_control as control
from tests.test_final import client


def wait_state(client,predicate):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=client.get('/api/v1/workspace/monitor-process')
        assert result.status_code==200,result.text
        value=result.json()
        if predicate(value):return value
        time.sleep(.1)
    raise AssertionError(value)


def test_managed_monitor_real_process_lifecycle(client,monkeypatch):
    monkeypatch.setattr(core,'LOCAL_DATA_ROOT',core.DB.parent)
    before=core.DB.read_bytes()
    body={'confirmed_enabled_monitors_only':True}
    assert client.post('/api/v1/workspace/monitor-process/start',json={**body,'command':'shell'}).status_code==422
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':1}).status_code==422
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':False}).status_code==422
    started=client.post('/api/v1/workspace/monitor-process/start',json=body)
    assert started.status_code==202,started.text
    identity=started.json()['service_id']
    try:
        current=wait_state(client,lambda v:v['state']=='current')
        assert current['managed'] and current['service_id']==identity
        assert client.post('/api/v1/workspace/monitor-process/start',json=body).status_code==409
        assert client.post('/api/v1/workspace/monitor-process/stop',json={'service_id':'f'*32}).status_code==409
        stopped=client.post('/api/v1/workspace/monitor-process/stop',json={'service_id':identity})
        assert stopped.status_code==202,stopped.text
        wait_state(client,lambda v:v['state']=='stopped')
        assert core.DB.read_bytes()==before
    finally:
        child=control._children.get(identity)
        if child and child.poll() is None:
            current=control.status()
            if current.get('managed') and current.get('service_id')==identity:
                control.stop(identity)
            child.wait(timeout=20)


def test_monitor_control_directory_rejects_symlink(client,monkeypatch,tmp_path):
    monkeypatch.setattr(core,'LOCAL_DATA_ROOT',tmp_path)
    outside=tmp_path/'outside';outside.mkdir()
    (tmp_path/'monitor-service').symlink_to(outside,target_is_directory=True)
    assert client.get('/api/v1/workspace/monitor-process').status_code==409
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':True}).status_code==409
    assert list(outside.iterdir())==[]


def test_unmanaged_monitor_cannot_be_replaced_or_stopped(client,monkeypatch):
    monkeypatch.setattr(control.service,'status',lambda:{'running':True,'state':'current'})
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':True}).status_code==409
    assert client.post('/api/v1/workspace/monitor-process/stop',json={'service_id':'a'*32}).status_code==409


def test_managed_monitor_survives_launcher_exit_and_new_controller_stops_it(client):
    before=core.DB.read_bytes()
    code="import sys,json;from pathlib import Path;import final_core as c;import monitor_process_control as m;c.DB=Path(sys.argv[1]);c.LOCAL_DATA_ROOT=Path(sys.argv[2]);print(json.dumps(m.start()))"
    launcher=subprocess.run([sys.executable,'-c',code,str(core.DB),str(core.LOCAL_DATA_ROOT)],
                            cwd=str(core.ROOT),capture_output=True,text=True,timeout=20)
    assert launcher.returncode==0,launcher.stderr
    identity=json.loads(launcher.stdout)['service_id']
    assert identity not in control._children
    try:
        current=wait_state(client,lambda v:v['state']=='current')
        assert current['managed'] and current['service_id']==identity
        assert client.post('/api/v1/workspace/monitor-process/stop',json={'service_id':identity}).status_code==202
        wait_state(client,lambda v:v['state']=='stopped')
        assert core.DB.read_bytes()==before
    finally:
        current=control.status()
        if current.get('managed') and current.get('service_id')==identity:
            control.stop(identity)
            wait_state(client,lambda v:v['state']=='stopped')


def test_heartbeat_does_not_follow_links_or_expose_extra_fields(client,tmp_path):
    from version import APP_VERSION
    outside=tmp_path/'outside.json'
    outside.write_text(json.dumps({'pid':123,'version':APP_VERSION,'last_tick':time.time(),'token':'heartbeat-private'}))
    path=control.service.lock_path('status.json')
    with control.service.process_lock('service.lock'):
        path.symlink_to(outside)
        response=client.get('/api/v1/workspace/monitor-process')
        assert response.status_code==200 and response.json()['state']=='starting'
        assert 'heartbeat-private' not in response.text
        path.unlink();path.write_text(outside.read_text())
        response=client.get('/api/v1/workspace/monitor-process')
        assert response.json()['state']=='current' and not response.json()['managed']
        assert 'heartbeat-private' not in response.text


def test_corrupt_managed_identity_fails_closed(client):
    control.service.lock_path('managed.json').write_text('{"unexpected":"value"}')
    assert client.get('/api/v1/workspace/monitor-process').status_code==409
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':True}).status_code==409


@pytest.mark.parametrize('kind',['fifo','oversized','public_directory'])
def test_invalid_control_storage_is_rejected_without_starting(client,kind):
    path=control.service.lock_path('managed.json')
    if kind=='fifo':os.mkfifo(path)
    elif kind=='oversized':path.write_text(json.dumps({'service_id':'a'*32})+' '*5000)
    else:path.parent.chmod(0o777)
    assert client.get('/api/v1/workspace/monitor-process').status_code==409
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':True}).status_code==409


def test_worker_does_not_inherit_desktop_session_environment(client,monkeypatch):
    captured={}
    for name in ('FIELDWORK_SESSION_TOKEN','FIELDWORK_DESKTOP_INSTANCE','FIELDWORK_PORT','FIELDWORK_DESKTOP_PARENT_PID'):
        monkeypatch.setenv(name,'fixture-desktop-value')
    def spawn(*args,**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=123,poll=lambda:None)
    monkeypatch.setattr(control.subprocess,'Popen',spawn)
    started=control.start()
    try:
        assert not set(captured['env'])&{'FIELDWORK_SESSION_TOKEN','FIELDWORK_DESKTOP_INSTANCE','FIELDWORK_PORT','FIELDWORK_DESKTOP_PARENT_PID'}
        assert captured['close_fds'] and captured['start_new_session']
    finally:
        control._children.pop(started['service_id'],None)
        control.service.lock_path('managed.json').unlink(missing_ok=True)


def test_orphan_launch_record_is_unresolved_not_live(client):
    control.service.lock_path('managed.json').write_text(json.dumps({'service_id':'a'*32,'state':'starting'}))
    state=client.get('/api/v1/workspace/monitor-process').json()
    assert state['state']=='unresolved' and not state['running'] and state['pid'] is None
    assert client.post('/api/v1/workspace/monitor-process/start',json={'confirmed_enabled_monitors_only':True}).status_code==409


def test_recover_orphan_fences_delayed_child(client):
    identity='c'*32
    control._write('managed.json',{'service_id':identity,'state':'starting'})
    assert client.post('/api/v1/workspace/monitor-process/recover',json={'service_id':'d'*32}).status_code==409
    assert client.post('/api/v1/workspace/monitor-process/recover',json={'service_id':identity}).status_code==200
    assert control.should_stop(identity)
    import threading
    import agent_monitor_service as service
    calls=[]
    service.run(threading.Event(),step=lambda:calls.append(True),interval=0,service_id=identity)
    assert calls==[]
    assert client.get('/api/v1/workspace/monitor-process').json()['state']=='stopped'
    assert client.post('/api/v1/workspace/monitor-process/recover',json={'service_id':identity}).status_code==409


def test_recover_refuses_held_execution_lock(client,monkeypatch):
    identity='e'*32
    control._write('managed.json',{'service_id':identity,'state':'starting'})
    import agent_monitor_service as service
    # Freeze the observation at the preceding unresolved state to exercise
    # acquisition of the execution lock, rather than only the status guard.
    monkeypatch.setattr(control,'status',lambda:{'state':'unresolved','service_id':identity})
    with service.process_lock('service.lock',blocking=False):
        assert client.post('/api/v1/workspace/monitor-process/recover',json={'service_id':identity}).status_code==409
        assert control._read('managed.json')['service_id']==identity
