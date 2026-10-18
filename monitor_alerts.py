"""Durable local policy-signal inbox. No external delivery or verification."""
import hashlib
from fastapi import HTTPException
import final_core as core

TOPIC = 'monitor-alerts'


def _write(db, entity, kind, payload):
    db.execute('INSERT INTO v5_events(topic,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)',
               (TOPIC, entity, kind, core.dump(payload), core.utcnow()))


def _configuration(db):
    row = db.execute("SELECT id,payload_json FROM v5_events WHERE topic=? AND event_type='monitor.alert_config' ORDER BY id DESC LIMIT 1", (TOPIC,)).fetchone()
    return {'revision': row['id'] if row else None, 'enabled': bool(core.load(row['payload_json'])['enabled']) if row else False,
            'delivery': 'local_inbox', 'applies_to': 'future_analysis_windows', 'independent_verification': False}


def configuration():
    with core.connect() as db:
        return _configuration(db)


def configure(enabled, expected_revision):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _configuration(db)
        if current['revision'] != expected_revision:
            raise HTTPException(409, 'Alert configuration changed; refresh')
        if current['enabled'] != enabled:
            _write(db, 'local', 'monitor.alert_config', {'enabled': enabled})
        return _configuration(db)


def dispatch(db, window):
    """Called inside the window transaction: commit signal and notification together."""
    if not window['signals'] or not _configuration(db)['enabled']:
        return
    if db.execute("SELECT 1 FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.alert_created'", (TOPIC, window['id'])).fetchone():
        return
    _write(db, window['id'], 'monitor.alert_created', {
        'audit_id': window['audit_id'], 'input_digest': window['input_digest'],
        'signal_count': len(window['signals']), 'verification_status': 'not_verified'})


def _checked(db, row):
    import agent_audit as audit
    value = core.load(row['payload_json'])
    parent = audit.audit_row(db, value['audit_id'])
    window_row = db.execute('SELECT * FROM agent_monitor_windows WHERE id=? AND audit_id=?', (row['entity_id'], value['audit_id'])).fetchone()
    if not window_row:
        raise HTTPException(409, 'Alert source window missing')
    window, _ = audit.read_artifact(db, window_row['artifact_id'], parent['run_id'])
    if (window['id'] != row['entity_id'] or window['audit_id'] != value['audit_id']
            or audit.digest(window['inputs']) != window['input_digest'] or window['input_digest'] != value['input_digest']
            or not window['signals'] or len(window['signals']) != value['signal_count']):
        raise HTTPException(409, 'Alert source integrity check failed')
    ack = db.execute("SELECT created_at FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.alert_acknowledged' ORDER BY id LIMIT 1", (TOPIC, row['entity_id'])).fetchone()
    item = {'id': row['entity_id'], 'audit_id': value['audit_id'], 'created_at': row['created_at'],
            'artifact_id': window_row['artifact_id'], 'signal_count': len(window['signals']),
            'signals': window['signals'], 'input_digest': window['input_digest'],
            'verification_status': 'not_verified', 'synthetic': bool(parent['demo']),
            'acknowledged_at': ack['created_at'] if ack else None}
    item['review_hash'] = hashlib.sha256(core.dump(item).encode()).hexdigest()
    return item


def inbox(offset=0, limit=20):
    with core.connect() as db:
        rows = db.execute("SELECT * FROM v5_events WHERE topic=? AND event_type='monitor.alert_created' ORDER BY id DESC LIMIT ? OFFSET ?", (TOPIC, limit+1, offset)).fetchall()
        return {'items': [_checked(db, row) for row in rows[:limit]], 'offset': offset, 'limit': limit,
                'has_more': len(rows)>limit, 'delivery': 'local_inbox'}


def acknowledge(window_id, review_hash):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.alert_created' ORDER BY id LIMIT 1", (TOPIC, window_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Alert not found')
        item = _checked(db, row)
        if item['review_hash'] != review_hash:
            raise HTTPException(409, 'Alert changed; review again')
        if not item['acknowledged_at']:
            _write(db, window_id, 'monitor.alert_acknowledged', {'input_digest': item['input_digest']})
        return _checked(db, row)


def source(window_id):
    """Read the exact frozen events and effective policy decisions, never the live audit."""
    import agent_audit as audit
    from reporting import redact_structure
    with core.connect() as db:
        row = db.execute("SELECT * FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.alert_created' ORDER BY id LIMIT 1", (TOPIC, window_id)).fetchone()
        if not row:
            raise HTTPException(404, 'Alert not found')
        item = _checked(db, row)
        window_row = db.execute('SELECT artifact_id FROM agent_monitor_windows WHERE id=?', (window_id,)).fetchone()
        parent = audit.audit_row(db, item['audit_id'])
        window, _ = audit.read_artifact(db, window_row['artifact_id'], parent['run_id'])
        ids = {signal['event_id'] for signal in window['signals']}
        events = []
        for event in window['inputs']['events']:
            if event['id'] not in ids:
                continue
            evaluation = window['policy_evaluations'][event['id']]
            events.append({key: event.get(key) for key in ('id', 'timestamp', 'actor', 'source_type', 'action_type', 'resource', 'status', 'trust_level', 'artifact_id', 'evidence_id', 'monitor_policy_id')}
                          | {'evaluation': evaluation})
        if len(events) != len(ids):
            raise HTTPException(409, 'Alert event binding is incomplete')
        return redact_structure({'alert': item, 'run_id': parent['run_id'], 'events': events,
                                 'rule_version': window['rule_version'], 'frozen_input_digest': window['input_digest'],
                                 'policy_versions': window['inputs']['monitor_policies'],
                                 'snapshot_policy': window['inputs']['snapshot'].get('policy'),
                                 'view': 'redacted_frozen_window', 'verification_status': 'not_verified'})


def native_delivery(after=None):
    """Read-only native notification feed. First read establishes a no-backfill cursor."""
    with core.connect() as db:
        latest=db.execute("SELECT COALESCE(MAX(id),0) FROM v5_events WHERE topic=? AND event_type='monitor.alert_created'",(TOPIC,)).fetchone()[0]
        enabled=_configuration(db)['enabled']
        if after is None or after>latest or not enabled:
            return {'cursor':latest,'items':[],'local_delivery_enabled':enabled,'history_backfilled':False}
        rows=db.execute("SELECT * FROM v5_events WHERE topic=? AND event_type='monitor.alert_created' AND id>? ORDER BY id LIMIT 50",(TOPIC,after)).fetchall()
        items=[]
        for row in rows:
            item=_checked(db,row)
            if not item['acknowledged_at']:
                # Deliberately exclude resource, event text, identity, policy and artifact bodies.
                items.append({'id':item['id'],'signal_count':item['signal_count']})
        return {'cursor':rows[-1]['id'] if rows else after,'items':items,
                'local_delivery_enabled':enabled,'history_backfilled':False}
