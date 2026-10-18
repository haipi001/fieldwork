from pathlib import Path
from types import SimpleNamespace
import plistlib
import pytest
import final_core as core
import monitor_startup as startup
import monitor_login_worker as worker
import monitor_process_control as control
import agent_monitor_service as service
from tests.test_final import client
_NATIVE_COMMAND=startup._command


@pytest.fixture
def launch_home(client,monkeypatch,tmp_path):
    home=tmp_path/'home';(home/'Library').mkdir(parents=True)
    monkeypatch.setattr(startup.Path,'home',classmethod(lambda cls:home))
    state={'registered':False,'commands':[]}
    def command(*args):
        state['commands'].append(args)
        if args[0]=='print':return SimpleNamespace(returncode=0 if state['registered'] else 113,stderr=b'' if state['registered'] else b'Could not find service')
        if args[0]=='bootstrap':state['registered']=True
        if args[0]=='bootout':state['registered']=False
        return SimpleNamespace(returncode=0,stderr=b'')
    monkeypatch.setattr(startup,'_command',command)
    return state


def test_login_configuration_is_reviewed_fixed_private_and_reversible(client,launch_home):
    before=core.DB.read_bytes()
    plan=client.get('/api/v1/workspace/monitor-startup').json()
    assert plan['configured'] is False
    label,path=startup._location();assert not path.exists()
    payload={'enabled':True,'confirmed_enabled_monitors_only':True,'preview_hash':plan['preview_hash']}
    assert client.post('/api/v1/workspace/monitor-startup',json={**payload,'confirmed_enabled_monitors_only':False}).status_code==422
    assert client.post('/api/v1/workspace/monitor-startup',json={**payload,'command':'shell'}).status_code==422
    result=client.post('/api/v1/workspace/monitor-startup',json=payload)
    assert result.status_code==200,result.text
    assert result.json()['state']=='enabled'
    spec=plistlib.loads(path.read_bytes())
    assert spec['RunAtLoad'] is True and spec['KeepAlive']=={'SuccessfulExit':False} and spec['ThrottleInterval']==30
    assert 'monitor_login_worker.py' in spec['ProgramArguments'][3]
    assert path.stat().st_mode&0o777==0o600
    assert 'FIELDWORK_SESSION_TOKEN' not in path.read_text()
    assert client.post('/api/v1/workspace/monitor-startup',json=payload).status_code==409
    current=result.json();again=client.post('/api/v1/workspace/monitor-startup',json={**payload,'preview_hash':current['preview_hash']})
    assert again.status_code==200
    assert sum(x[0]=='bootstrap' for x in launch_home['commands'])==1
    disabled=client.post('/api/v1/workspace/monitor-startup',json={**payload,'enabled':False,'preview_hash':current['preview_hash']})
    assert disabled.status_code==200 and not path.exists() and not launch_home['registered']
    assert core.DB.read_bytes()==before


def test_login_job_rejects_foreign_file_and_running_manual_process(client,launch_home,monkeypatch):
    plan=startup.plan();label,path=startup._location()
    monkeypatch.setattr(control,'status',lambda:{'running':True,'state':'current'})
    with pytest.raises(RuntimeError):startup.apply(True,plan['preview_hash'])
    assert not path.exists()
    path.parent.mkdir();path.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':['unknown']}));path.chmod(0o600)
    assert client.get('/api/v1/workspace/monitor-startup').status_code==409
    assert not any(x[0]=='bootout' for x in launch_home['commands'])


def test_login_worker_only_recovers_its_own_orphan_identity(client,launch_home):
    control._write('managed.json',{'service_id':'a'*32,'state':'starting'})
    with pytest.raises(RuntimeError):worker.claim_login_instance()
    control._write('login-owned.json',{'service_id':'a'*32})
    identifier=worker.claim_login_instance()
    assert identifier!='a'*32
    assert control.should_stop('a'*32) is True
    assert control.should_stop(identifier) is False
    assert control._read('login-owned.json')['service_id']==identifier


def test_login_entry_real_process_stops_normally_without_enabling_collection(client,launch_home):
    import subprocess,sys
    from tests.test_monitor_process_control import wait_state
    before=core.DB.read_bytes()
    child=subprocess.Popen([sys.executable,'-E','-s',str(core.ROOT/'monitor_login_worker.py'),
        '--database',str(core.DB),'--data-root',str(core.LOCAL_DATA_ROOT),'--expected-version',startup.APP_VERSION],
        cwd=core.ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        current=wait_state(client,lambda value:value.get('state')=='current')
        assert current['pid']==child.pid
        control.stop(current['service_id'])
        assert child.wait(timeout=20)==0
        assert control.status()['state']=='stopped'
        assert core.DB.read_bytes()==before
    finally:
        if child.poll() is None:
            child.terminate();child.wait(timeout=10)


def test_restore_is_blocked_by_configured_login_job_even_when_process_is_stopped(client,launch_home,tmp_path,monkeypatch):
    from tests.test_v5_workspace_ui import _backup_artifact
    _backup_artifact(client,tmp_path,monkeypatch)
    archive=client.post('/api/v1/workspace/evidence-backups',json={}).json()
    current=startup.plan();startup.apply(True,current['preview_hash'])
    before=core.DB.read_bytes()
    result=client.get('/api/v1/workspace/evidence-backups/'+archive['id']+'/restore-plan')
    assert result.status_code==200,result.text
    assert 'monitor_login_configured' in result.json()['blockers']
    assert result.json()['preflight_ready'] is False
    assert core.DB.read_bytes()==before


@pytest.mark.skipif(__import__('os').environ.get('FIELDWORK_TEST_LOGIN_AGENT')!='1',reason='explicit isolated macOS launchd acceptance')
def test_real_launchd_registration_crash_restart_normal_stop_and_cleanup(client,launch_home,monkeypatch):
    import time,os
    monkeypatch.setattr(startup,'_command',_NATIVE_COMMAND)
    label,path=startup._location();before=core.DB.read_bytes()
    def wait(predicate,timeout=60):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=control.status()
            if predicate(value):return value
            time.sleep(.2)
        raise AssertionError(value)
    try:
        plan=startup.plan();assert not plan['configured'] and not plan['registered']
        installed=startup.apply(True,plan['preview_hash'])
        assert installed['state']=='enabled'
        first=wait(lambda value:value.get('state')=='current')
        assert control._read('login-owned.json')['service_id']==first['service_id']
        killed=startup._command('kill','SIGKILL',f'gui/{os.getuid()}/{label}')
        assert killed.returncode==0
        second=wait(lambda value:value.get('state')=='current' and value.get('service_id')!=first['service_id'],timeout=75)
        assert second['pid']!=first['pid']
        control.stop(second['service_id'])
        wait(lambda value:value.get('state')=='stopped')
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            assert control.status()['state']=='stopped'
            time.sleep(.5)
        assert core.DB.read_bytes()==before
    finally:
        current=startup.plan()
        if current['configured'] or current['registered']:startup.apply(False,current['preview_hash'])
        assert not startup._registered(label)
        assert not path.exists()
