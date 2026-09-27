import time
import subprocess
import sys
import json
import pytest
import native_ai_session as session
from test_native_ai_reader import Pipeline
from test_native_ai_forwarder import record


class Flow(Pipeline):
    pending=None
    def __init__(self):super().__init__();self.offline=False;self.flushes=0
    def flush(self):
        self.flushes+=1
        if self.offline:raise ConnectionError()
    def persist(self):pass


def test_launch_failure_keeps_source_unavailable(monkeypatch):
    def fail(*args):raise session.NativeLaunchError()
    monkeypatch.setattr(session,'launch_installed_collector',fail)
    flow=session.NativeCollectorSession(Flow(),'ABCDEFGHIJ')
    assert flow.start()['status']=='unavailable'
    assert flow.tick()['error_code']=='launch_requirements'


def test_child_permission_exit_is_not_reported_as_connected(monkeypatch):
    child=subprocess.Popen([sys.executable,'-c','raise SystemExit(78)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    monkeypatch.setattr(session,'launch_installed_collector',lambda *a:child)
    flow=session.NativeCollectorSession(Flow(),'ABCDEFGHIJ');flow.start()
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=flow.tick()
            if state['status']=='exited':break
            time.sleep(.01)
        assert state['error_code']=='activation_failed' and state['events_read']==0
    finally:flow.stop()


def test_retry_backoff_and_stop_report_durability(monkeypatch):
    output=(json.dumps(record())+'\n').encode()
    child=subprocess.Popen([sys.executable,'-c',f'import os,time;os.write(1,{output!r});time.sleep(10)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    monkeypatch.setattr(session,'launch_installed_collector',lambda *a:child)
    pipe=Flow();pipe.offline=True;flow=session.NativeCollectorSession(pipe,'ABCDEFGHIJ');flow.start()
    try:
        deadline=time.monotonic()+5
        while pipe.flushes==0 and time.monotonic()<deadline:flow.tick();time.sleep(.01)
        assert flow.health()['status']=='retrying'
        attempts=pipe.flushes;flow.tick();assert pipe.flushes==attempts
        stopped=flow.stop()
        assert stopped['status']=='stopped' and stopped['unsaved_records']==1
        assert child.poll() is not None
    finally:
        if flow.status!='stopped':flow.stop()


def test_stop_persists_real_pipeline_without_attempting_network(tmp_path,monkeypatch):
    from native_ai_pipeline import NativePipeline
    from native_ai_queue import EncryptedPendingQueue
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    class NoNetwork:
        def post(self,*args):pytest.fail('stop must not wait for network')
        def get_receipt(self,*args):pytest.fail('stop must not wait for network')
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    monkeypatch.setattr(session,'launch_installed_collector',lambda *a:child)
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        pipe=NativePipeline(queue=queue,transport=NoNetwork(),private_key=Ed25519PrivateKey.generate(),
            audit_id='audit',collector_id='collector',session_id='session')
        flow=session.NativeCollectorSession(pipe,'ABCDEFGHIJ');flow.start();pipe.accept(record())
        stopped=flow.stop(grace=0)
        assert stopped['pending_batch'] and stopped['unsaved_records']==0
        assert queue.load() is not None and stopped['producer_alive'] is False
        assert flow.stop()==stopped
