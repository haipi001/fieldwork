"""Backend pairing preparation. Never installs, elevates, or starts a collector."""
import base64
import hashlib
import json

import native_ai_binding
from native_ai_control import decode_request


class PairingUnavailable(ValueError):
    pass


def scoped_request(command,pairing):
    """Status/stop requests for exactly the audit that was paired by the backend."""
    if command not in {'status','stop'}:raise ValueError('未知的配对控制操作')
    value=decode_request(pairing)
    if value['command']!='start':raise ValueError('控制操作需要完整启动配对')
    value={key:value[key] for key in ('audit_id','collector_id','session_id')}
    value.update(version=1,command=command)
    raw=json.dumps(value,separators=(',',':')).encode()
    decode_request(raw)
    return raw


def prepare_pairing(db,audit_id,*,new_id,now):
    """Use protected installed identity and server-owned audit session only.

    Caller owns its database transaction and monitor lifecycle lock. Registration
    is idempotent; an explicitly revoked key remains revoked.
    """
    row=db.execute('SELECT a.identity_json,m.status,m.collector_kind FROM agent_audits a '
        'JOIN agent_monitors m ON m.audit_id=a.id WHERE a.id=?',(audit_id,)).fetchone()
    if row is None or row['collector_kind']!='desktop' or row['status']!='active':
        raise PairingUnavailable('本机监控未运行，不能连接原生采集器')
    identity=json.loads(row['identity_json'])
    registration=native_ai_binding.read_registration()
    public=registration['public_key']
    fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(base64.b64decode(public,validate=True)).digest()).decode().rstrip('=')
    collector=db.execute('SELECT * FROM agent_collectors WHERE fingerprint=?',(fingerprint,)).fetchone()
    if collector is not None:
        if collector['status']!='active' or collector['algorithm']!='Ed25519' or collector['public_key']!=public:
            raise PairingUnavailable('原生采集器身份已撤销或登记不一致')
        collector_id=collector['id']
    else:
        collector_id=new_id()
    value={'version':1,'command':'start','audit_id':audit_id,'collector_id':collector_id,
        'session_id':identity['session_id'],'public_key':public}
    raw=json.dumps(value,separators=(',',':')).encode()
    decode_request(raw)
    if collector is None:
        db.execute('INSERT INTO agent_collectors VALUES(?,?,?,?,?,?,?,?,?)',
            (collector_id,'本机文件动作采集器','native-'+hashlib.sha256(public.encode()).hexdigest(),
             'Ed25519',public,fingerprint,'active',now,None))
    return raw
