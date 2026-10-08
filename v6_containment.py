"""Containment state derived from append-only events, with historical-row fallback."""


def containment_state(db, run_id):
    event = db.execute('SELECT * FROM run_containment_events_v6 WHERE run_id=? ORDER BY id DESC LIMIT 1',
                       (run_id,)).fetchone()
    if event:
        return {'active': event['state'] == 'contained', 'generation': event['id']}
    legacy = db.execute('SELECT 1 FROM run_containment_v6 WHERE run_id=?', (run_id,)).fetchone()
    return {'active': bool(legacy), 'generation': 0}


def is_contained(db, run_id):
    return containment_state(db, run_id)['active']
