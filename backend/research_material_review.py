"""Bounded read-only registered-source checks for draft review, not verification."""
import hashlib
import json
from pathlib import Path
import final_core as core
from proof_materials import read_proof_file


def source_status(db, node, run_id, budget):
    """Return fixed status codes only; never expose paths or file contents."""
    kind, ref = node['source_type'], node['source_ref']
    attrs = core.load(node['attributes_json']) or {}
    if not isinstance(attrs, dict):
        return 'changed'
    artifact_id = None
    if kind == 'artifact':
        artifact_id = ref
    elif kind == 'observation':
        source = db.execute('SELECT * FROM observations WHERE id=? AND run_id=?', (ref, run_id)).fetchone()
        if not source:
            return 'missing'
        ownership=attrs.get('ownership_projection') is True
        title='HTTP ownership projection · '+source['subject'] if ownership else source['summary']
        body=source['summary'] if ownership else source['subject']
        if (title != node['title'] or body != node['body']
                or (ownership and (source['observation_type'] not in {'http.ownership_projection','http.ownership_denial'} or attrs.get('subject')!=source['subject']))
                or source['raw_ref'] != attrs.get('artifact_ref')
                or any(source[k] != attrs.get(k) for k in ('mode','observation_type','source_capability'))):
            return 'changed'
        artifact_id = source['raw_ref']
    elif kind == 'evidence_v2':
        source = db.execute('SELECT * FROM evidence_v2 WHERE id=? AND run_id=?', (ref, run_id)).fetchone()
        if not source:
            return 'missing'
        if (source['summary'] != node['title'] or any(source[k] != attrs.get(k)
                for k in ('evidence_type', 'polarity', 'observation_id', 'artifact_id'))):
            return 'changed'
        artifact_id = source['artifact_id']
        if source['observation_id']:
            observation = db.execute('SELECT * FROM observations WHERE id=? AND run_id=?',
                                     (source['observation_id'], run_id)).fetchone()
            if not observation:
                return 'missing'
            if not artifact_id:
                artifact_id = observation['raw_ref']
    else:
        return 'unregistered'
    if not artifact_id:
        return 'unregistered'
    artifact = db.execute('SELECT * FROM artifacts WHERE id=? AND run_id=?', (artifact_id, run_id)).fetchone()
    if not artifact:
        return 'missing'
    projection=(kind=='artifact' and attrs.get('ownership_projection') is True
                or kind=='observation' and attrs.get('observation_type') in {'http.ownership_projection','http.ownership_denial'}
                or kind=='evidence_v2' and attrs.get('evidence_type')=='http.ownership_denial')
    if projection and artifact['kind']!='http.ownership_projection':return 'changed'
    if kind == 'artifact' and artifact['sha256'] != attrs.get('sha256'):
        return 'changed'
    if artifact_id in budget['checked']:
        return budget['checked'][artifact_id]
    try:
        path = Path(artifact['uri'])
        path.relative_to(core.LOCAL_DATA_ROOT)
        raw = read_proof_file(path, min(2_000_000, budget['remaining']))
        budget['remaining'] -= len(raw)
        status = 'verified' if hashlib.sha256(raw).hexdigest() == artifact['sha256'] else 'changed'
        if status=='verified' and artifact['kind']=='http.ownership_projection':
            from guided_capture import projection_current
            status='verified' if projection_current(db,run_id,json.loads(raw)) else 'changed'
    except (OSError, ValueError, TypeError):
        status = 'unavailable'
    budget['checked'][artifact_id] = status
    return status
