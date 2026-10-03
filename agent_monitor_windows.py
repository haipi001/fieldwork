"""Bounded, durable policy-analysis windows; never a vulnerability receipt.

Original events and their provenance remain in the collector audit. Each window
pins its inputs, rule version and result in an integrity-checked artifact.
"""
import final_core as core
import time
from fastapi import HTTPException

RULE_VERSION = 'agent-policy-window-v1'


def init_db(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS agent_monitor_windows (
      id TEXT PRIMARY KEY, audit_id TEXT NOT NULL REFERENCES agent_monitors(audit_id),
      artifact_id TEXT NOT NULL REFERENCES artifacts(id), created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS agent_monitor_windows_audit ON agent_monitor_windows(audit_id,created_at);
    CREATE TABLE IF NOT EXISTS agent_monitor_window_events (
      event_id TEXT PRIMARY KEY REFERENCES agent_events(id),
      window_id TEXT NOT NULL REFERENCES agent_monitor_windows(id)
    );
    CREATE TABLE IF NOT EXISTS agent_monitor_analysis_state (
      audit_id TEXT PRIMARY KEY REFERENCES agent_monitors(audit_id),
      failures INTEGER NOT NULL DEFAULT 0, retry_after REAL NOT NULL DEFAULT 0,
      last_error TEXT
    );
    CREATE TABLE IF NOT EXISTS agent_monitor_policies (
      id TEXT PRIMARY KEY, audit_id TEXT NOT NULL REFERENCES agent_monitors(audit_id),
      artifact_id TEXT NOT NULL REFERENCES artifacts(id), created_at TEXT NOT NULL
    );
    ''')


def current_policy(db, audit_id):
    return db.execute('SELECT * FROM agent_monitor_policies WHERE audit_id=? ORDER BY rowid DESC LIMIT 1', (audit_id,)).fetchone()


def read_policy(db, parent, policy_id):
    import agent_audit as audit
    row = db.execute('SELECT * FROM agent_monitor_policies WHERE id=? AND audit_id=?', (policy_id, parent['id'])).fetchone()
    if not row:
        raise HTTPException(409, '缺少事件绑定的监控策略版本')
    value, _ = audit.read_artifact(db, row['artifact_id'], parent['run_id'])
    if value['id'] != policy_id or value['audit_id'] != parent['id']:
        raise HTTPException(409, 'Integrity Check Failed: Monitor Policy 改变')
    return value


def policy_view(audit_id):
    import agent_audit as audit
    with core.connect() as db:
        parent = audit.audit_row(db, audit_id)
        row = current_policy(db, audit_id)
        return read_policy(db, parent, row['id']) if row else {'id': None, 'policy': None}


def configure_policy(audit_id, policy, expected_id):
    import agent_audit as audit
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        parent = audit.audit_row(db, audit_id)
        monitor = db.execute('SELECT status FROM agent_monitors WHERE audit_id=?', (audit_id,)).fetchone()
        if not monitor or monitor['status'] not in {'active', 'paused'} or parent['analysis_json']:
            raise HTTPException(409, '只能配置活动或暂停的监控')
        old = current_policy(db, audit_id)
        if (old['id'] if old else None) != expected_id:
            raise HTTPException(409, '策略已由其他操作更新，请重新读取后确认')
        if old and read_policy(db, parent, old['id'])['policy'] == policy:
            return read_policy(db, parent, old['id'])
        pid, now = core.uid('monitor-policy'), core.utcnow()
        value = {'id': pid, 'audit_id': audit_id, 'created_at': now, 'policy': policy,
                 'applies_to': 'subsequently_ingested_events', 'previous_id': expected_id}
        aid, _ = audit.artifact(db, parent['run_id'], 'agent_monitor_policy', value)
        db.execute('INSERT INTO agent_monitor_policies VALUES(?,?,?,?)', (pid, audit_id, aid, now))
        db.execute('INSERT INTO audit_log(engagement_id,action,detail,created_at) VALUES(?,?,?,?)',
                   (audit_id, 'agent_monitor.policy_configured', core.dump({'policy_id': pid}), now))
    return value


def scheduled_tick(audit_id):
    with core.connect() as db:
        state = db.execute('SELECT * FROM agent_monitor_analysis_state WHERE audit_id=?', (audit_id,)).fetchone()
    if state and state['retry_after'] > time.time():
        return None
    try:
        result = tick(audit_id)
    except Exception as error:
        failures = (state['failures'] if state else 0) + 1
        with core.connect() as db:
            db.execute('INSERT OR REPLACE INTO agent_monitor_analysis_state VALUES(?,?,?,?)',
                       (audit_id, failures, time.time() + min(300, 2 ** min(failures, 8)), type(error).__name__))
        return None
    with core.connect() as db:
        db.execute('DELETE FROM agent_monitor_analysis_state WHERE audit_id=?', (audit_id,))
    return result


def tick(audit_id, limit=200):
    import agent_audit as audit
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError('window limit must be 1..500')
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        monitor = db.execute('SELECT status FROM agent_monitors WHERE audit_id=?', (audit_id,)).fetchone()
        if not monitor or monitor['status'] != 'active':
            return None
        parent = audit.audit_row(db, audit_id)
        if parent['analysis_json']:
            raise HTTPException(409, '监控输入已冻结，不能继续增量分析')
        rows = db.execute('''SELECT e.* FROM agent_events e WHERE e.audit_id=?
            AND NOT EXISTS (SELECT 1 FROM agent_monitor_window_events w WHERE w.event_id=e.id)
            ORDER BY e.rowid LIMIT ?''', (audit_id, limit)).fetchall()
        if not rows:
            return None
        snapshot = audit.checked_snapshot(db, parent)
        events = []
        for row in rows:
            event, _ = audit.read_artifact(db, row['artifact_id'], parent['run_id'])
            if event != core.load(row['event_json']) or event['id'] != row['id'] or event['audit_id'] != audit_id:
                raise HTTPException(409, 'Integrity Check Failed: Window Event 改变')
            events.append({**event, 'artifact_id': row['artifact_id'], 'evidence_id': row['evidence_id']})
        policies = {pid: read_policy(db, parent, pid) for pid in {e.get('monitor_policy_id') for e in events} if pid}
        inputs = {'snapshot': snapshot, 'events': events, 'monitor_policies': policies}
        effective = {e['id']: policies[e['monitor_policy_id']]['policy'] if e.get('monitor_policy_id') else snapshot['policy'] for e in events}
        evaluations = {event['id']: audit.evaluate_policy(event, effective[event['id']]) for event in events}
        signals = [{'event_id': event['id'], 'boundaries': evaluations[event['id']]['boundaries']}
                   for event in events if event['status'] in audit.SUCCESS and evaluations[event['id']]['decision'] == 'violation']
        wid, now = core.uid('window'), core.utcnow()
        result = {'id': wid, 'audit_id': audit_id, 'rule_version': RULE_VERSION,
                  'created_at': now, 'input_digest': audit.digest(inputs), 'inputs': inputs,
                  'policy_evaluations': evaluations, 'signals': signals,
                  'status': 'analyzed' if all(effective.values()) else 'policy_required',
                  'verification_status': 'not_verified',
                  'boundary': 'Recorded policy signals only; no independent replay or vulnerability confirmation.'}
        artifact_id, _ = audit.artifact(db, parent['run_id'], 'agent_monitor_window', result)
        db.execute('INSERT INTO agent_monitor_windows VALUES(?,?,?,?)', (wid, audit_id, artifact_id, now))
        db.executemany('INSERT INTO agent_monitor_window_events VALUES(?,?)', [(e['id'], wid) for e in events])
    return {'id': wid, 'event_count': len(events), 'signal_count': len(signals), 'status': result['status']}


def recent(audit_id):
    import agent_audit as audit
    with core.connect() as db:
        parent = audit.audit_row(db, audit_id)
        rows = db.execute('SELECT * FROM agent_monitor_windows WHERE audit_id=? ORDER BY rowid DESC LIMIT 20', (audit_id,)).fetchall()
        result = []
        for row in rows:
            window, _ = audit.read_artifact(db, row['artifact_id'], parent['run_id'])
            if window['id'] != row['id'] or window['audit_id'] != audit_id or audit.digest(window['inputs']) != window['input_digest']:
                raise HTTPException(409, 'Integrity Check Failed: Window 改变')
            result.append({key: window[key] for key in ('id', 'created_at', 'rule_version', 'status', 'verification_status', 'signals', 'input_digest')}
                          | {'event_count': len(window['inputs']['events']), 'artifact_id': row['artifact_id']})
        pending = db.execute('''SELECT COUNT(*) FROM agent_events e WHERE e.audit_id=?
            AND NOT EXISTS (SELECT 1 FROM agent_monitor_window_events w WHERE w.event_id=e.id)''', (audit_id,)).fetchone()[0]
        state = db.execute('SELECT failures,retry_after,last_error FROM agent_monitor_analysis_state WHERE audit_id=?', (audit_id,)).fetchone()
    return {'windows': result, 'pending_events': pending, 'limit': 20, 'recovery': dict(state) if state else None}
