"""Bounded signed native batches; caller owns protected keys and queue lifecycle."""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone

import native_ai_events


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class ImportConflict(Exception):
    """Transport maps HTTP 409 here; only matching receipts resolve conflicts."""


class PendingBatch:
    """Freeze exactly the envelope that must be retried after a lost reply."""
    def __init__(self, records, *, audit_id, collector_id, sequence, private_key, session_id):
        if not 1 <= len(records) <= 100:
            raise ValueError('每批原生记录必须为 1–100 条')
        if type(sequence) is not int or sequence < 1:
            raise ValueError('签名序列必须为正整数')
        events = [native_ai_events.normalize(record, session_id) for record in records]
        content = canonical({'events': events}).decode()
        if len(content.encode()) > 1_000_000:
            raise ValueError('原生批次超过大小上限')
        envelope = dict(kind='generic_json', content=content, provenance='operator_telemetry',
            source_name='native Endpoint Security metadata', independent_attested=False,
            self_report_complete=False, collector_id=collector_id, sequence=sequence,
            nonce=secrets.token_hex(16), signed_at=datetime.now(timezone.utc).isoformat())
        self.audit_id = audit_id
        self.payload_hash = hashlib.sha256(content.encode()).hexdigest()
        payload = {key: envelope[key] for key in ('collector_id','sequence','nonce','signed_at','kind','provenance','source_name')}
        payload.update(schema='fieldwork-agent-telemetry-signature/1', audit_id=audit_id, input_sha256=self.payload_hash)
        envelope['signature'] = base64.b64encode(private_key.sign(canonical(payload))).decode()
        # Immutable bytes, not a dictionary that a caller could mutate between retries.
        self.request_bytes = canonical(envelope)

    def matches_receipt(self, receipt):
        envelope = json.loads(self.request_bytes)
        return isinstance(receipt, dict) and all(receipt.get(key) == expected for key, expected in {
            'audit_id': self.audit_id, 'collector_id': envelope['collector_id'],
            'sequence': envelope['sequence'], 'nonce': envelope['nonce'],
            'payload_sha256': self.payload_hash, 'signature': envelope['signature']}.items())

    def deliver(self, post, get_receipt):
        """Transport callbacks must enforce loopback, no redirects and finite timeouts.

        An uncertain POST is successful only if a matching committed receipt exists.
        Otherwise re-raise, retaining this exact object for the next retry.
        """
        try:
            return post(self.audit_id, self.request_bytes)
        except (ConnectionError, TimeoutError, ImportConflict):
            envelope = json.loads(self.request_bytes)
            receipt = get_receipt(self.audit_id, envelope['collector_id'], envelope['sequence'])
            if self.matches_receipt(receipt):
                return {'acknowledged': True, 'reconciled': True}
            raise
