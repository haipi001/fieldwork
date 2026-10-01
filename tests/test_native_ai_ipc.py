import json
import os
import platform
import socket
import struct
import threading
import tempfile
import time
import pytest
import native_ai_ipc as ipc
from native_ai_control import NativeControl


MAC=platform.system()=='Darwin'
REQUEST=b'{"version":1,"command":"status"}'


@pytest.mark.skipif(not MAC,reason='Real macOS peer credentials')
def test_real_unix_peer_credentials_come_from_os():
    left,right=socket.socketpair()
    try:
        assert ipc.peer_identity(left)==(os.geteuid(),os.getegid())
        assert ipc.peer_identity(right)==(os.geteuid(),os.getegid())
    finally:left.close();right.close()


def test_non_unix_transport_is_rejected():
    with socket.socket() as connection:
        with pytest.raises(PermissionError):ipc.peer_identity(connection)


@pytest.fixture
def control():return NativeControl(app_uid=os.geteuid() or 1,team_id='ABCDEFGHIJ')


def exchange(control,body,monkeypatch,*,fragment=False,timeout=.5):
    client,server=socket.socketpair()
    # Framing tests use a fixed authenticated account; real credentials have a separate test.
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(control.app_uid,0))
    thread=threading.Thread(target=ipc.handle_connection,args=(server,control),kwargs={'timeout':timeout})
    thread.start()
    try:
        frame=struct.pack('!I',len(body))+body
        if fragment:
            for byte in frame:client.sendall(bytes([byte]))
        else:client.sendall(frame)
        value=json.loads(ipc._read_frame(client,ipc.MAX_RESPONSE,time.monotonic()+2))
        return value
    finally:client.close();thread.join(2)


def test_fragmented_request_reaches_control_once(control,monkeypatch):
    assert exchange(control,REQUEST,monkeypatch,fragment=True)=={'ok':True,'status':'inactive','producer_alive':False}


def test_request_uid_cannot_override_os_account(control,monkeypatch):
    raw=json.dumps({'version':1,'command':'status','peer_uid':control.app_uid}).encode()
    assert exchange(control,raw,monkeypatch)['error_code']=='invalid_request'


def test_unauthorized_peer_rejected_without_reading(control,monkeypatch):
    client,server=socket.socketpair()
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(control.app_uid+1,0))
    try:
        result=ipc.handle_connection(server,control,timeout=.05)
        assert result['error_code']=='account_not_authorized'
        assert json.loads(ipc._read_frame(client,ipc.MAX_RESPONSE,time.monotonic()+1))==result
    finally:client.close()


@pytest.mark.parametrize('length',[0,ipc.MAX_REQUEST+1,2**32-1])
def test_oversized_or_empty_frame_rejected_before_body(control,monkeypatch,length):
    client,server=socket.socketpair()
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(control.app_uid,0))
    client.sendall(struct.pack('!I',length))
    try:assert ipc.handle_connection(server,control,timeout=.05)['error_code']=='invalid_control_frame'
    finally:client.close()


def test_idle_or_incomplete_request_is_bounded(control,monkeypatch):
    client,server=socket.socketpair()
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(control.app_uid,0))
    try:
        before=time.monotonic()
        assert ipc.handle_connection(server,control,timeout=.05)['error_code']=='control_read_timeout'
        assert time.monotonic()-before<1
    finally:client.close()


def test_broken_frame_does_not_invoke_control(control,monkeypatch):
    client,server=socket.socketpair()
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(control.app_uid,0))
    client.sendall(struct.pack('!I',20)+b'{}');client.shutdown(socket.SHUT_WR)
    try:assert ipc.handle_connection(server,control)['error_code']=='invalid_control_frame'
    finally:client.close()


@pytest.fixture
def listener(monkeypatch):
    # Short path accommodates macOS sockaddr_un; root directory protection is mocked only here.
    with tempfile.TemporaryDirectory(prefix='fw-ipc-',dir='/tmp') as directory:
        from pathlib import Path
        path=Path(directory)/'control.sock'
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));server.listen(1)
        server.settimeout(2)
        monkeypatch.setattr(ipc,'SOCKET_PATH',path)
        def identity():
            info=path.lstat();return info.st_dev,info.st_ino,info.st_mtime_ns
        monkeypatch.setattr(ipc,'_validate_endpoint',identity)
        try:yield server
        finally:server.close()


@pytest.mark.skipif(not MAC or os.geteuid()==0,reason='Real non-root macOS peer')
def test_client_rejects_real_nonroot_server_before_sending(listener):
    received=[]
    def accept():
        with listener.accept()[0] as connection:received.append(connection.recv(1024))
    thread=threading.Thread(target=accept);thread.start()
    try:
        with pytest.raises(PermissionError,match='管理员进程'):ipc.request(REQUEST,timeout=1)
    finally:thread.join(2)
    assert received==[b'']


@pytest.mark.parametrize('response',[b'{"ok":true,"status":"inactive","producer_alive":false}',
    b'{"ok":true,"private_key":"unexpected"}',b'{"ok":1}',
    b'{"ok":true,"events_read":NaN}',b'{"ok":true,"ok":false}'])
def test_client_roundtrip_and_strict_response(listener,monkeypatch,response):
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(0,0))
    received=[]
    def accept():
        with listener.accept()[0] as connection:
            received.append(ipc._read_frame(connection,ipc.MAX_REQUEST,time.monotonic()+1))
            connection.sendall(struct.pack('!I',len(response))+response)
    thread=threading.Thread(target=accept);thread.start()
    try:
        if b'"status"' in response:assert ipc.request(REQUEST,timeout=1)['status']=='inactive'
        else:
            with pytest.raises(ValueError):ipc.request(REQUEST,timeout=1)
    finally:thread.join(2)
    assert received==[REQUEST]


def test_client_rejects_endpoint_change_before_transmission(listener,monkeypatch):
    monkeypatch.setattr(ipc,'peer_identity',lambda connection:(0,0))
    identities=iter([(1,2,3),(1,4,3)])
    monkeypatch.setattr(ipc,'_validate_endpoint',lambda:next(identities))
    received=[]
    def accept():
        with listener.accept()[0] as connection:received.append(connection.recv(1024))
    thread=threading.Thread(target=accept);thread.start()
    try:
        with pytest.raises(PermissionError,match='发生变化'):ipc.request(REQUEST,timeout=1)
    finally:thread.join(2)
    assert received==[b'']


def test_unprotected_socket_directory_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(ipc,'SOCKET_PATH',tmp_path/'control.sock')
    tmp_path.chmod(0o777)
    with pytest.raises(PermissionError):ipc._validate_endpoint()
