import json
import hashlib
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from native_ai_pipeline import NativePipeline
from native_ai_queue import EncryptedPendingQueue
from native_ai_forwarder import ImportConflict
from test_native_ai_forwarder import record


class Transport:
    def __init__(self):self.bodies=[];self.offline=False;self.receipts={}
    def post(self,aid,body):
        self.bodies.append(body)
        if self.offline:raise ConnectionError()
        value=json.loads(body)
        index=(aid,value['collector_id'],value['sequence'])
        if index in self.receipts:raise ImportConflict()
        self.receipts[index]=dict(audit_id=aid,collector_id=value['collector_id'],sequence=value['sequence'],
            nonce=value['nonce'],signature=value['signature'],payload_sha256=hashlib.sha256(value['content'].encode()).hexdigest())
        return {'acknowledged':True}
    def get_receipt(self,*args):return self.receipts.get(args)


def pipeline(queue,transport,key):
    return NativePipeline(queue=queue,transport=transport,private_key=key,
        audit_id='audit',collector_id='collector',session_id='session')


def test_pipeline_restart_preserves_batch_and_persistent_next_sequence(tmp_path):
    key=Ed25519PrivateKey.generate();transport=Transport()
    def open_queue():return EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector')
    with open_queue() as queue:
        flow=pipeline(queue,transport,key);flow.accept(record());transport.offline=True
        with pytest.raises(ConnectionError):flow.flush()
        original=transport.bodies[-1]
        with pytest.raises(BufferError):flow.accept(record())
    with open_queue() as queue:
        flow=pipeline(queue,transport,key);transport.offline=False
        flow.flush()
        assert transport.bodies[-1]==original
        assert queue.next_sequence()==2 and queue.load() is None
    with open_queue() as queue:
        flow=pipeline(queue,transport,key);flow.accept(record());flow.flush()
        assert json.loads(transport.bodies[-1])['sequence']==2
        assert queue.next_sequence()==3


def test_pipeline_disk_failure_never_sends_or_drops_memory_batch(tmp_path,monkeypatch):
    key=Ed25519PrivateKey.generate();transport=Transport()
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        flow=pipeline(queue,transport,key);flow.accept(record())
        def failed(*args):raise OSError('fixture failure')
        monkeypatch.setattr(queue,'save',failed)
        with pytest.raises(OSError):flow.flush()
        assert len(flow.records)==1 and not transport.bodies


def test_cleanup_failure_reconciles_same_batch_and_does_not_reuse_sequence(tmp_path,monkeypatch):
    import os
    key=Ed25519PrivateKey.generate();transport=Transport()
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        flow=pipeline(queue,transport,key);flow.accept(record())
        original_unlink=os.unlink
        def failed(name,*args,**kwargs):
            if name=='pending.enc':raise OSError('fixture cleanup failure')
            return original_unlink(name,*args,**kwargs)
        monkeypatch.setattr(os,'unlink',failed)
        with pytest.raises(OSError):flow.flush()
        assert queue.next_sequence()==2
        saved=queue.load()
        monkeypatch.setattr(os,'unlink',original_unlink)
        restored=pipeline(queue,transport,key);restored.flush()
        assert transport.bodies[-1]==saved
        assert queue.next_sequence()==2 and queue.load() is None


def test_buffer_bound_rejects_without_losing_prior_records(tmp_path):
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        flow=pipeline(queue,Transport(),Ed25519PrivateKey.generate())
        for _ in range(100):flow.accept(record())
        with pytest.raises(BufferError):flow.accept(record())
        assert len(flow.records)==100


def test_missing_ack_retains_encrypted_batch(tmp_path):
    transport=Transport()
    transport.post=lambda *args:None
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        flow=pipeline(queue,transport,Ed25519PrivateKey.generate());flow.accept(record())
        with pytest.raises(ValueError,match='明确提交确认'):flow.flush()
        assert queue.load() is not None and queue.next_sequence()==1


def test_byte_budget_applies_before_record_count_limit(tmp_path):
    with EncryptedPendingQueue(tmp_path/'queue',b'k'*32,audit_id='audit',collector_id='collector') as queue:
        flow=pipeline(queue,Transport(),Ed25519PrivateKey.generate())
        large=record()
        for name in ('actor','process_executable','process_started_at','attribution_method','filesystem_path','resource','destination'):
            large[name]='x'*5000
        with pytest.raises(BufferError):
            for _ in range(100):flow.accept(large)
        assert len(flow.records)<100 and flow.buffer_bytes<=1_000_000
