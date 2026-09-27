import base64
import os
import json
import subprocess
import sys
import native_ai_session
import pytest
import native_ai_runtime as runtime
from test_native_ai_forwarder import record
from test_native_ai_pipeline import Transport


@pytest.fixture
def installed(tmp_path,monkeypatch):
    key_directory=tmp_path/'keys';key_directory.mkdir(mode=0o700)
    monkeypatch.setattr(runtime.native_ai_keys,'_root_required',lambda:None)
    monkeypatch.setattr(runtime.native_ai_keys,'_directory_fd',lambda:os.open(key_directory,os.O_RDONLY|os.O_DIRECTORY))
    monkeypatch.setattr(runtime,'verify_installed_collector',lambda team:True)
    public=runtime.native_ai_keys.load_or_create().public_key_base64()
    monkeypatch.setattr(runtime,'register_installation',lambda team:{'public_key':public})
    monkeypatch.setattr(runtime,'_queue_directory',lambda scope:tmp_path/scope)
    return dict(team_id='ABCDEFGHIJ',audit_id='audit',collector_id='collector',session_id='session',public_key=public)


def test_runtime_restores_pending_with_real_persistent_keys(installed):
    transport=Transport();transport.offline=True
    first=runtime.prepare(**installed)
    assert first.session.status=='inactive' and first.session.process is None
    first.pipeline.transport=transport;first.pipeline.accept(record())
    with pytest.raises(ConnectionError):first.pipeline.flush()
    frozen=first.pipeline.pending.request_bytes
    first.close()
    second=runtime.prepare(**installed)
    try:
        assert second.pipeline.pending.request_bytes==frozen
        transport.offline=False;second.pipeline.transport=transport;second.pipeline.flush()
        assert transport.bodies[-1]==frozen and second.queue.next_sequence()==2
    finally:second.close()


def test_mismatched_pairing_and_remote_url_are_rejected(installed,tmp_path):
    with pytest.raises(ValueError):runtime.prepare(**{**installed,'public_key':base64.b64encode(b'x'*32).decode()})
    with pytest.raises(ValueError):runtime.prepare(**installed,base_url='https://example.com')
    assert sorted(path.name for path in tmp_path.iterdir())==['keys']


@pytest.mark.parametrize('change',[{'audit_id':'../escape'},{'collector_id':''},{'session_id':'bad\nvalue'},
    {'public_key':None},{'public_key':'bad-base64'}])
def test_invalid_pairing_is_rejected(installed,change):
    with pytest.raises(ValueError):runtime.prepare(**{**installed,**change})


def test_closed_runtime_cannot_restart_and_context_closes_queue(installed):
    with runtime.prepare(**installed) as service:
        assert service.queue.fd is not None
    assert service.closed and service.queue.fd is None
    with pytest.raises(ValueError):service.start()
    with pytest.raises(ValueError):service.tick()
    service.close()


def test_active_runtime_excludes_second_consumer(installed):
    first=runtime.prepare(**installed)
    try:
        with pytest.raises(ValueError,match='已有服务'):runtime.prepare(**installed)
        with pytest.raises(ValueError,match='已有服务'):runtime.prepare(**{**installed,'audit_id':'another-audit'})
    finally:first.close()
    runtime.prepare(**installed).close()


def test_new_audit_keeps_collector_sequence_increasing(installed):
    transport=Transport()
    for index in range(3):
        with runtime.prepare(**{**installed,'audit_id':f'audit-{index}','session_id':f'session-{index}'}) as service:
            service.pipeline.transport=transport
            service.pipeline.accept(record());service.pipeline.flush()
            assert json.loads(transport.bodies[-1])['sequence']==index+1
            assert json.loads(transport.bodies[-1])['collector_id']==installed['collector_id']


def test_drain_delivers_stream_larger_than_one_batch_before_closing(installed,monkeypatch):
    line=json.dumps(record())+'\n'
    program=f'import os,signal\nsignal.signal(signal.SIGTERM,lambda *_:None)\nos.write(2,b"R")\ndata={(line*150).encode()!r}\nwhile data:\n data=data[os.write(1,data):]\n'
    child=subprocess.Popen([sys.executable,'-c',program],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert child.stderr.read(1)==b'R'
    monkeypatch.setattr(native_ai_session,'launch_installed_collector',lambda *_:child)
    service=runtime.prepare(**installed);transport=Transport();service.pipeline.transport=transport
    try:
        service.start();result=service.drain()
        assert service.closed and result['status']=='stopped' and result['stdout_drained']
        assert sum(len(json.loads(json.loads(body)['content'])['events']) for body in transport.bodies)==150
        assert not result['pending_batch'] and result['unsaved_records']==0
    finally:
        if not service.closed:service.close()


def test_drain_delivery_failure_retains_reader_and_frozen_batch_for_retry(installed,monkeypatch):
    line=json.dumps(record())+'\n'
    child=subprocess.Popen([sys.executable,'-c',f'import os;os.write(1,{line.encode()!r})'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    monkeypatch.setattr(native_ai_session,'launch_installed_collector',lambda *_:child)
    service=runtime.prepare(**installed);transport=Transport();transport.offline=True;service.pipeline.transport=transport
    try:
        service.start()
        child.wait(timeout=3)
        with pytest.raises(ConnectionError):service.drain()
        frozen=service.pipeline.pending.request_bytes
        assert not service.closed and service.session.reader.selector is not None
        transport.offline=False;result=service.drain()
        assert transport.bodies[-1]==frozen and service.closed and result['stdout_drained']
    finally:
        if not service.closed:service.close()


def test_failed_batch_storage_consumes_reservation_without_dropping_records(installed,monkeypatch):
    with runtime.prepare(**installed) as service:
        service.pipeline.accept(record())
        save=service.queue.save
        monkeypatch.setattr(service.queue,'save',lambda *_:(_ for _ in ()).throw(OSError('fixture')))
        with pytest.raises(OSError):service.pipeline.persist()
        assert len(service.pipeline.records)==1 and service.pipeline.pending is None
        monkeypatch.setattr(service.queue,'save',save)
        service.pipeline.persist()
        assert json.loads(service.pipeline.pending.request_bytes)['sequence']==2


def test_close_persistence_failure_retains_queue_and_can_retry(installed,monkeypatch):
    service=runtime.prepare(**installed);service.pipeline.accept(record())
    save=service.queue.save
    def failed(*args):raise OSError('fixture storage failure')
    monkeypatch.setattr(service.queue,'save',failed)
    with pytest.raises(OSError):service.close()
    assert not service.closed and service.queue.fd is not None and service.pipeline.records
    monkeypatch.setattr(service.queue,'save',save)
    service.close()
    restored=runtime.prepare(**installed)
    try:assert restored.pipeline.pending is not None
    finally:restored.close()
