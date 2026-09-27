"""Backend pairing and installed-service control; never installs or elevates."""
import base64
import hashlib
import json

import native_ai_binding
from native_ai_control import decode_request
import native_ai_ipc


class PairingUnavailable(ValueError):
    pass


def init_db(db):
    db.execute('''CREATE TABLE IF NOT EXISTS agent_native_connections (
        audit_id TEXT PRIMARY KEY REFERENCES agent_audits(id) ON DELETE CASCADE,
        pairing BLOB, status TEXT NOT NULL, error_code TEXT)''')


def view(db,audit_id):
    row=db.execute('SELECT status,error_code FROM agent_native_connections WHERE audit_id=?',(audit_id,)).fetchone()
    return dict(row) if row else {'status':'not_connected','error_code':None}


def connect(database,audit_id,*,new_id,now):
    """Caller holds lifecycle lock. Commit pairing before any ambiguous IPC send."""
    with database() as db:
        previous=db.execute('SELECT pairing FROM agent_native_connections WHERE audit_id=?',(audit_id,)).fetchone()
        try:pairing=prepare_pairing(db,audit_id,new_id=new_id,now=now)
        except (OSError,ValueError):
            # Never discard a previously attempted pairing when installation changes.
            if previous and previous['pairing'] is not None:
                db.execute("UPDATE agent_native_connections SET status='connection_uncertain',error_code='installation_unavailable' WHERE audit_id=?",(audit_id,))
            else:
                db.execute("INSERT OR REPLACE INTO agent_native_connections VALUES(?,NULL,'not_connected','installation_unavailable')",(audit_id,))
            return view(db,audit_id)
        if previous and previous['pairing'] is not None and previous['pairing']!=pairing:
            raise PairingUnavailable('旧配对尚未完成交付，不能替换')
        db.execute("INSERT OR REPLACE INTO agent_native_connections VALUES(?,?,'starting',NULL)",(audit_id,pairing))
    try:result=native_ai_ipc.request(pairing)
    except (OSError,ValueError):result={'ok':False,'error_code':'service_unavailable'}
    with database() as db:
        # Ready describes IPC startup, never independently verified collection.
        db.execute('UPDATE agent_native_connections SET status=?,error_code=? WHERE audit_id=?',
            ('service_ready' if result.get('ok') is True else 'connection_uncertain',
             None if result.get('ok') is True else result.get('error_code','service_unavailable'),audit_id))
        return view(db,audit_id)


def drain(database,audit_id):
    """Keep the audit accepting records until this returns success.

    A missing response is not proof of shutdown; preserve pairing for retry.
    """
    with database() as db:
        row=db.execute('SELECT pairing FROM agent_native_connections WHERE audit_id=?',(audit_id,)).fetchone()
        if row is None or row['pairing'] is None:return True
        pairing=row['pairing']
    try:result=native_ai_ipc.request(scoped_request('drain',pairing))
    except (OSError,ValueError):result={'ok':False}
    complete=result.get('ok') is True
    with database() as db:
        db.execute('UPDATE agent_native_connections SET status=?,error_code=? WHERE audit_id=?',
            ('inactive' if complete else 'delivery_pending',None if complete else 'delivery_pending',audit_id))
    return complete


def scoped_request(command,pairing):
    """Status/stop requests for exactly the audit that was paired by the backend."""
    if command not in {'status','stop','drain'}:raise ValueError('未知的配对控制操作')
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
