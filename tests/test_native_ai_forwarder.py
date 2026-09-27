import copy
import json
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from native_ai_forwarder import PendingBatch, ImportConflict


def record():
    return dict(schema='fieldwork-native-es/1', synthetic=False, timestamp='2026-09-26T01:00:00Z',
        actor='Cursor', source_type='filesystem', action_type='unknown',status='observed',
        command_category='native_open', process_id=42,parent_process_id=1,process_pid_version=7,
        process_started_at='1.000000',process_executable='Cursor',attribution_method='executable_match',
        filesystem_path='/fixture',resource='/fixture',destination='',modified=False,mapped_writable=False,
        native_sequence=2,collector_dropped=0,kernel_dropped=0,native_authorization='allowed',
        path_truncated=False,destination_truncated=False,executable_truncated=False)


def batch(records=None):
    return PendingBatch(records or [record()],audit_id='audit',collector_id='collector',sequence=1,
        private_key=Ed25519PrivateKey.generate(),session_id='session')


def receipt(value):
    envelope=json.loads(value.request_bytes)
    return dict(audit_id='audit',collector_id='collector',sequence=1,nonce=envelope['nonce'],
        signature=envelope['signature'],payload_sha256=value.payload_hash)


def test_lost_ack_reconciles_exact_committed_receipt():
    records=[record()]; pending=batch(records); original=pending.request_bytes
    records[0]['actor']='changed'
    def lost(audit_id, body):
        assert body == original
        raise TimeoutError()
    assert pending.deliver(lost,lambda *args:receipt(pending))['reconciled'] is True
    assert pending.request_bytes == original
    assert not pending.matches_receipt({**receipt(pending),'sequence':True})
    for key in receipt(pending):
        mismatch=copy.deepcopy(receipt(pending));mismatch[key]='wrong'
        assert not pending.matches_receipt(mismatch)
    with pytest.raises(TimeoutError):pending.deliver(lost,lambda *args:None)


def test_reject_simulation_and_overlarge_batches_before_signing():
    with pytest.raises(ValueError):batch([{**record(),'synthetic':True}])
    with pytest.raises(ValueError):batch([record()]*101)


def test_http_rejection_is_not_treated_as_lost_ack():
    pending=batch()
    def rejected(*args):raise ValueError('rejected')
    def forbidden(*args):pytest.fail('must not reconcile a definitive rejection')
    with pytest.raises(ValueError):pending.deliver(rejected,forbidden)


def test_replayed_retry_only_succeeds_with_matching_receipt():
    pending=batch()
    def conflict(*args):raise ImportConflict()
    assert pending.deliver(conflict,lambda *args:receipt(pending))['acknowledged'] is True
    with pytest.raises(ImportConflict):pending.deliver(conflict,lambda *args:None)


def test_restored_batch_must_match_key_audit_and_session():
    from cryptography.exceptions import InvalidSignature
    key=Ed25519PrivateKey.generate()
    pending=PendingBatch([record()],audit_id='audit',collector_id='collector',sequence=1,
        private_key=key,session_id='session')
    kwargs=dict(audit_id='audit',collector_id='collector',session_id='session',public_key=key.public_key())
    restored=PendingBatch.restore(pending.request_bytes,**kwargs)
    assert restored.request_bytes==pending.request_bytes
    assert restored.matches_receipt(receipt(pending))
    with pytest.raises(InvalidSignature):PendingBatch.restore(pending.request_bytes,**{**kwargs,'audit_id':'other'})
    with pytest.raises(InvalidSignature):PendingBatch.restore(pending.request_bytes,**{**kwargs,'public_key':Ed25519PrivateKey.generate().public_key()})
    with pytest.raises(ValueError):PendingBatch.restore(pending.request_bytes,**{**kwargs,'session_id':'other'})


def test_restart_retries_same_encrypted_signed_batch_until_receipt(tmp_path):
    from native_ai_queue import EncryptedPendingQueue
    key=Ed25519PrivateKey.generate(); encryption=b'e'*32; path=tmp_path/'queue'
    pending=PendingBatch([record()],audit_id='audit',collector_id='collector',sequence=1,
        private_key=key,session_id='session')
    def open_queue():return EncryptedPendingQueue(path,encryption,audit_id='audit',collector_id='collector')
    with open_queue() as queue:queue.save(pending.request_bytes)
    with open_queue() as restarted:
        recovered=PendingBatch.restore(restarted.load(),audit_id='audit',collector_id='collector',
            session_id='session',public_key=key.public_key())
        def unavailable(*args):raise ConnectionError()
        with pytest.raises(ConnectionError):recovered.deliver(unavailable,lambda *args:None)
        assert restarted.load()==pending.request_bytes
        assert recovered.deliver(unavailable,lambda *args:receipt(pending))['acknowledged']
        restarted.acknowledge(recovered.request_bytes)
        assert restarted.load() is None
