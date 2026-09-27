import base64
import json
import sqlite3
import pytest
import native_ai_bridge as bridge


@pytest.fixture
def db(monkeypatch):
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.executescript('''CREATE TABLE agent_audits(id TEXT PRIMARY KEY,identity_json TEXT);
        CREATE TABLE agent_monitors(audit_id TEXT,status TEXT,collector_kind TEXT);
        CREATE TABLE agent_collectors(id TEXT PRIMARY KEY,name TEXT,key_id TEXT UNIQUE,algorithm TEXT,
        public_key TEXT,fingerprint TEXT UNIQUE,status TEXT,created_at TEXT,revoked_at TEXT);''')
    connection.execute('INSERT INTO agent_audits VALUES(?,?)',('audit',json.dumps({'session_id':'server-session'})))
    connection.execute('INSERT INTO agent_monitors VALUES(?,?,?)',('audit','active','desktop'))
    monkeypatch.setattr(bridge.native_ai_binding,'read_registration',lambda:{'public_key':base64.b64encode(b'x'*32).decode()})
    yield connection
    connection.close()


def pair(db):return bridge.prepare_pairing(db,'audit',new_id=lambda:'collector',now='fixture')


def test_pairing_uses_installed_key_and_server_session_idempotently(db):
    first=pair(db);second=pair(db)
    assert first==second
    value=json.loads(first)
    assert value['session_id']=='server-session' and value['audit_id']=='audit'
    assert value['collector_id']=='collector' and 'team_id' not in value
    assert db.execute('SELECT COUNT(*) FROM agent_collectors').fetchone()[0]==1


@pytest.mark.parametrize('status,kind',[('paused','desktop'),('stopped','desktop'),('active','fieldwork')])
def test_inactive_or_internal_monitor_never_reads_installation(db,monkeypatch,status,kind):
    db.execute('UPDATE agent_monitors SET status=?,collector_kind=?',(status,kind))
    monkeypatch.setattr(bridge.native_ai_binding,'read_registration',lambda:pytest.fail('must not read'))
    with pytest.raises(bridge.PairingUnavailable):pair(db)
    assert db.execute('SELECT COUNT(*) FROM agent_collectors').fetchone()[0]==0


def test_missing_audit_rejected(db):
    with pytest.raises(bridge.PairingUnavailable):bridge.prepare_pairing(db,'missing',new_id=lambda:'collector',now='fixture')


def test_revocation_cannot_be_undone_by_auto_pairing(db):
    pair(db);db.execute("UPDATE agent_collectors SET status='revoked'")
    with pytest.raises(bridge.PairingUnavailable):pair(db)
    assert db.execute('SELECT status FROM agent_collectors').fetchone()[0]=='revoked'


def test_registration_failure_creates_no_database_identity(db,monkeypatch):
    def unavailable():raise PermissionError('fixture')
    monkeypatch.setattr(bridge.native_ai_binding,'read_registration',unavailable)
    with pytest.raises(PermissionError):pair(db)
    assert db.execute('SELECT COUNT(*) FROM agent_collectors').fetchone()[0]==0


def test_invalid_server_session_rejected_before_registration(db):
    db.execute('UPDATE agent_audits SET identity_json=?',(json.dumps({'session_id':'bad\nvalue'}),))
    with pytest.raises(ValueError):pair(db)
    assert db.execute('SELECT COUNT(*) FROM agent_collectors').fetchone()[0]==0


def test_existing_same_key_is_reused_in_another_audit(db):
    pair(db)
    db.execute('INSERT INTO agent_audits VALUES(?,?)',('second',json.dumps({'session_id':'second-session'})))
    db.execute('INSERT INTO agent_monitors VALUES(?,?,?)',('second','active','desktop'))
    value=json.loads(bridge.prepare_pairing(db,'second',new_id=lambda:pytest.fail('identity must be reused'),now='fixture'))
    assert value['collector_id']=='collector' and value['session_id']=='second-session'


@pytest.mark.parametrize('command',['status','stop','drain'])
def test_control_request_keeps_pairing_scope_and_omits_key(db,command):
    value=json.loads(bridge.scoped_request(command,pair(db)))
    assert value=={'version':1,'command':command,'audit_id':'audit','collector_id':'collector','session_id':'server-session'}


def test_control_request_rejects_non_start_pairing_and_unknown_command(db):
    with pytest.raises(ValueError):bridge.scoped_request('shell',pair(db))
    with pytest.raises(ValueError):bridge.scoped_request('stop',b'{"version":1,"command":"status"}')
