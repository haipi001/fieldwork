import json
import os
import socket
import struct
import tempfile
import time
import signal
import threading
from pathlib import Path
import pytest
import native_ai_service as service


def test_service_signal_stops_loop_and_restores_handlers(monkeypatch):
    calls=[];handlers={};original={n:signal.getsignal(n) for n in (signal.SIGTERM,signal.SIGINT)}
    monkeypatch.setattr(service,'load_control',lambda:object())
    def install(number,handler):handlers[number]=handler;calls.append(('signal',number,handler))
    monkeypatch.setattr(service.signal,'signal',install)
    class Listener:
        def __init__(self,control):calls.append('created')
        def serve_once(self,timeout):
            assert timeout==.25
            calls.append('tick');handlers[signal.SIGTERM](signal.SIGTERM,None)
        def close(self):calls.append('closed')
    monkeypatch.setattr(service,'NativeListener',Listener)
    service.run_service()
    assert calls.count('tick')==1 and 'closed' in calls
    assert all(handlers[n] is original[n] for n in original)


@pytest.mark.parametrize('failure',['tick','close','create'])
def test_service_failure_restores_signal_handlers(monkeypatch,failure):
    original={n:signal.getsignal(n) for n in (signal.SIGTERM,signal.SIGINT)};handlers={};closed=[]
    monkeypatch.setattr(service,'load_control',lambda:object())
    monkeypatch.setattr(service.signal,'signal',lambda n,h:handlers.update({n:h}))
    class Listener:
        def __init__(self,control):
            if failure=='create':raise OSError('fixture')
        def serve_once(self,timeout):
            if failure=='tick':raise OSError('fixture')
            handlers[signal.SIGINT](signal.SIGINT,None)
        def close(self):
            closed.append(True)
            if failure=='close':raise OSError('fixture')
    monkeypatch.setattr(service,'NativeListener',Listener)
    with pytest.raises(OSError):service.run_service()
    assert bool(closed)==(failure!='create')
    assert all(handlers[n] is original[n] for n in original)


def test_service_refuses_worker_thread_before_loading(monkeypatch):
    monkeypatch.setattr(service.threading,'current_thread',lambda:object())
    monkeypatch.setattr(service,'load_control',lambda:pytest.fail('must not load'))
    with pytest.raises(ValueError,match='主线程'):service.run_service()


@pytest.fixture
def configuration(tmp_path,monkeypatch):
    path=tmp_path/'service.json'
    path.write_text(json.dumps({'schema':'fieldwork-native-service/1','app_uid':501,
        'team_id':'ABCDEFGHIJ','base_url':'http://127.0.0.1:8000'}));path.chmod(0o600)
    monkeypatch.setattr(service,'CONFIG_PATH',path)
    monkeypatch.setattr(service.native_ai_keys,'_root_required',lambda:None)
    def identity(path):
        info=path.lstat();return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns
    monkeypatch.setattr(service,'protected_file',identity)
    monkeypatch.setattr(service.pwd,'getpwuid',lambda uid:object())
    return path


def test_configuration_builds_control_without_launching(configuration):
    control=service.load_control()
    assert control.app_uid==501 and control.team_id=='ABCDEFGHIJ' and control.runtime is None


@pytest.mark.parametrize('change',[{'app_uid':0},{'app_uid':True},{'base_url':'https://example.com'},
    {'executable':'/tmp/other'},{'team_id':'bad'},{'schema':'wrong'}])
def test_bad_configuration_is_rejected(configuration,change):
    value=json.loads(configuration.read_bytes());value.update(change);configuration.write_text(json.dumps(value))
    with pytest.raises(ValueError):service.load_control()


def test_configuration_rejects_permissions_and_duplicate_fields(configuration):
    configuration.chmod(0o644)
    with pytest.raises(PermissionError):service.load_control()
    configuration.chmod(0o600);configuration.write_bytes(b'{"schema":"x","schema":"y"}')
    with pytest.raises(ValueError):service.load_control()


def test_configuration_rejects_changes_during_read(configuration,monkeypatch):
    original=service.protected_file;calls=[]
    def changed(path):
        if calls:path.write_bytes(path.read_bytes()+b' ')
        calls.append(1);return original(path)
    monkeypatch.setattr(service,'protected_file',changed)
    with pytest.raises(ValueError,match='发生变化'):service.load_control()


def test_configuration_size_and_unknown_account_are_rejected(configuration,monkeypatch):
    original=configuration.read_bytes()
    configuration.write_bytes(b' '*4097)
    with pytest.raises(ValueError,match='超过上限'):service.load_control()
    configuration.write_bytes(original)
    def unknown(uid):raise KeyError('fixture')
    monkeypatch.setattr(service.pwd,'getpwuid',unknown)
    with pytest.raises(ValueError,match='账户不存在'):service.load_control()


def test_configuration_links_are_rejected(configuration):
    original=configuration.with_name('original.json');configuration.rename(original)
    configuration.symlink_to(original)
    with pytest.raises(OSError):service.load_control()


def test_unprotected_listener_parent_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'SOCKET_PATH',tmp_path/'control.sock');tmp_path.chmod(0o777)
    with pytest.raises(PermissionError):service._parent_fd()


class Control:
    app_uid=os.geteuid()
    def __init__(self):self.ticks=0;self.can_stop=True
    def tick(self):self.ticks+=1
    def shutdown(self):return {'ok':self.can_stop}
    def handle(self,raw,*,peer_uid):
        assert peer_uid==self.app_uid
        return {'ok':True,'status':'inactive','producer_alive':False}


@pytest.fixture
def listener_directory(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='fw-service-',dir='/tmp') as directory:
        path=Path(directory)/'control.sock'
        monkeypatch.setattr(service,'SOCKET_PATH',path)
        monkeypatch.setattr(service.native_ai_keys,'_root_required',lambda:None)
        monkeypatch.setattr(service,'_parent_fd',lambda:os.open(directory,os.O_RDONLY|os.O_DIRECTORY))
        monkeypatch.setattr(service.os,'chown',lambda *args:None)
        monkeypatch.setattr(service.ipc,'peer_identity',lambda connection:(os.geteuid(),os.getegid()))
        yield path


def test_listener_serves_one_request_and_cleans_owned_socket(listener_directory):
    control=Control()
    with service.NativeListener(control) as listener:
        assert listener_directory.stat().st_mode&0o777==0o600
        raw=b'{"version":1,"command":"status"}'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.connect(str(listener_directory));client.sendall(struct.pack('!I',len(raw))+raw)
            assert listener.serve_once(0)['status']=='inactive'
            response=service.ipc._read_frame(client,8192,time.monotonic()+1)
            assert json.loads(response)['ok']
        assert control.ticks==1
        with pytest.raises(ValueError):listener.serve_once(float('nan'))
    assert not listener_directory.exists()
    with pytest.raises(ValueError):listener.serve_once(0)


def test_second_listener_cannot_replace_active_one(listener_directory):
    with service.NativeListener(Control()) as first:
        identity=listener_directory.stat().st_ino
        with pytest.raises(ValueError,match='已有原生'):service.NativeListener(Control())
        assert listener_directory.stat().st_ino==identity and first.socket.fileno()>=0


def test_stale_owned_socket_recovery_and_foreign_file_rejection(listener_directory):
    old=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);old.bind(str(listener_directory));listener_directory.chmod(0o600)
    try:
        with service.NativeListener(Control()):assert listener_directory.exists()
    finally:old.close()
    listener_directory.write_bytes(b'foreign fixture')
    with pytest.raises(PermissionError):service.NativeListener(Control())
    assert listener_directory.read_bytes()==b'foreign fixture'


def test_active_endpoint_without_cooperative_lock_is_not_removed(listener_directory):
    old=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);old.bind(str(listener_directory))
    listener_directory.chmod(0o600);old.listen(1)
    try:
        identity=listener_directory.stat().st_ino
        with pytest.raises(ValueError,match='活动控制'):service.NativeListener(Control())
        assert listener_directory.stat().st_ino==identity
    finally:old.close()


def test_close_failure_retains_listener_for_retry(listener_directory):
    control=Control();listener=service.NativeListener(control);control.can_stop=False
    with pytest.raises(OSError):listener.close()
    assert not listener.closed and listener.socket.fileno()>=0 and listener_directory.exists()
    control.can_stop=True;listener.close()
    assert listener.closed and not listener_directory.exists()


def test_cleanup_does_not_delete_replacement_file(listener_directory):
    listener=service.NativeListener(Control())
    listener_directory.unlink();listener_directory.write_bytes(b'replacement fixture')
    listener.close()
    assert listener_directory.read_bytes()==b'replacement fixture'


def test_normal_user_cannot_load_or_publish(monkeypatch):
    def refused():raise PermissionError('fixture root gate')
    monkeypatch.setattr(service.native_ai_keys,'_root_required',refused)
    with pytest.raises(PermissionError):service.load_control()
    with pytest.raises(PermissionError):service.NativeListener(Control())
