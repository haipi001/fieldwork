"""Bounded signed native batches; caller owns protected keys and queue lifecycle."""
import base64
import hashlib
import json
import secrets
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import native_ai_events


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class ImportConflict(Exception):
    """Transport maps HTTP 409 here; only matching receipts resolve conflicts."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LoopbackTransport:
    """HTTP transport to a literal local address; ignores environment proxies."""
    def __init__(self, base_url='http://127.0.0.1:8000', timeout=5):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1'}
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
            raise ValueError('原生转发只允许本机 HTTP 根地址')
        if parsed.port is None or not 1 <= parsed.port <= 65535:
            raise ValueError('本机地址需要有效端口')
        if type(timeout) not in {int,float} or not 0 < timeout <= 30:
            raise ValueError('请求超时必须在 0–30 秒之间')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    @staticmethod
    def _id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',value):
            raise ValueError('无效的审计或采集器标识')
        return value

    def _request(self, path, body=None, receipt=False):
        request = urllib.request.Request(self.base_url + '/api/v1/agent-audit' + path,
            data=body, headers={'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                if response.status != 200:
                    raise ValueError('本机服务返回非预期响应')
                if not receipt:
                    # Import responses may contain the entire timeline; never buffer it.
                    return {'acknowledged':True}
                content = response.read(65537)
                if len(content) > 65536:
                    raise ValueError('批次回执超过大小上限')
                value = json.loads(content)
                if not isinstance(value,dict):
                    raise ValueError('批次回执格式无效')
                return value
        except urllib.error.HTTPError as error:
            error.close()
            if receipt and error.code == 404:
                return None
            if error.code == 409 and not receipt:
                raise ImportConflict('本机服务拒绝当前批次') from None
            if error.code >= 500:
                raise ConnectionError('本机服务暂不可用') from None
            raise ValueError('本机服务拒绝请求或重定向') from None
        except urllib.error.URLError:
            raise ConnectionError('无法连接本机服务') from None

    def post(self, audit_id, body):
        if not isinstance(body,bytes) or len(body) > 2_000_000:
            raise ValueError('转发批次格式或大小无效')
        return self._request('/audits/' + self._id(audit_id) + '/imports',body)

    def get_receipt(self,audit_id,collector_id,sequence):
        if type(sequence) is not int or sequence < 1:
            raise ValueError('无效的批次序列')
        return self._request('/audits/' + self._id(audit_id) + '/collectors/' +
            self._id(collector_id) + '/receipts/' + str(sequence),receipt=True)


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
        return isinstance(receipt, dict) and all(type(receipt.get(key)) is type(expected) and receipt.get(key) == expected for key, expected in {
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
