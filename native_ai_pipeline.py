"""Connect native validation, durable batches and loopback delivery.

The service must supply protected keys and stop/pause lifecycle decisions.
"""
import json
from native_ai_forwarder import PendingBatch, canonical
import native_ai_events


class NativePipeline:
    def __init__(self, *, queue, transport, private_key, audit_id, collector_id, session_id):
        self.queue=queue;self.transport=transport;self.private_key=private_key
        self.audit_id=audit_id;self.collector_id=collector_id;self.session_id=session_id
        self.records=[];self.pending=None
        self.buffer_bytes=len(canonical({'events':[]}))
        saved=queue.load()
        if saved is not None:
            self.pending=PendingBatch.restore(saved,audit_id=audit_id,collector_id=collector_id,
                session_id=session_id,public_key=private_key.public_key())

    def accept(self, record):
        # Caller must apply backpressure instead of reading unbounded process output.
        if self.pending is not None or len(self.records)>=100:
            raise BufferError('待提交批次尚未确认，请暂停读取')
        normalized=native_ai_events.normalize(record,self.session_id)
        size=len(canonical(normalized))+1
        if self.buffer_bytes+size>1_000_000:
            raise BufferError('本批次已达到大小限制，请先提交')
        self.records.append(dict(record))
        self.buffer_bytes+=size

    def persist(self):
        if self.pending is None:
            if not self.records:return
            pending=PendingBatch(self.records,audit_id=self.audit_id,collector_id=self.collector_id,
                sequence=self.queue.next_sequence(),private_key=self.private_key,session_id=self.session_id)
            # Persist before sending. A disk failure retains the original in-memory records.
            self.queue.save(pending.request_bytes)
            self.pending=pending;self.records=[];self.buffer_bytes=len(canonical({'events':[]}))

    def flush(self):
        self.persist()
        if self.pending is None:return {'acknowledged':True,'empty':True}
        result=self.pending.deliver(self.transport.post,self.transport.get_receipt)
        if not isinstance(result,dict) or result.get('acknowledged') is not True:
            raise ValueError('未获得明确提交确认，保留待提交批次')
        sequence=json.loads(self.pending.request_bytes)['sequence']
        self.queue.acknowledge(self.pending.request_bytes,next_sequence=sequence+1)
        self.pending=None
        return result
