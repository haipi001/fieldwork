"""Operator response history bound to existing Agent Audit incidents and evidence."""
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

import final_core as core
from v6_intents import LOCAL_SESSION_PRINCIPAL

router = APIRouter(prefix='/api/v1/v6', tags=['V6 Incident Response'])
STATES = ('DETECTED', 'TRIAGED', 'CONTAINED', 'INVESTIGATING', 'REMEDIATING', 'RECOVERED', 'CLOSED')
State = Literal['DETECTED', 'TRIAGED', 'CONTAINED', 'INVESTIGATING', 'REMEDIATING', 'RECOVERED', 'CLOSED']


class Transition(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_state: State
    state: State
    artifact_id: str


def _incident(db, candidate_id):
    row = db.execute('SELECT c.run_id FROM candidate_findings c JOIN agent_incidents i '
                     'ON i.candidate_id=c.id WHERE c.id=?', (candidate_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Agent Audit incident not found')
    return row


def _history(db, candidate_id):
    return [dict(row) for row in db.execute('SELECT * FROM incident_response_events_v6 '
                'WHERE candidate_id=? ORDER BY id', (candidate_id,))]


@router.get('/incidents/{candidate_id}/response')
def response_history(candidate_id: str):
    with core.connect() as db:
        incident = _incident(db, candidate_id)
        history = _history(db, candidate_id)
        return {'candidate_id': candidate_id, 'run_id': incident['run_id'],
                'state': history[-1]['state'] if history else 'DETECTED', 'history': history,
                'state_authority': 'operator_response_record',
                'run_containment_active': bool(db.execute('SELECT 1 FROM run_containment_v6 WHERE run_id=?',
                                                          (incident['run_id'],)).fetchone()),
                'containment_boundary': 'subsequent_v6_authorized_operations'}


@router.post('/incidents/{candidate_id}/response')
def transition_response(candidate_id: str, body: Transition):
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        incident = _incident(db, candidate_id)
        history = _history(db, candidate_id)
        current = history[-1]['state'] if history else 'DETECTED'
        if current != body.expected_state or STATES.index(body.state) != STATES.index(current) + 1:
            raise HTTPException(409, 'Incident response state changed or transition is invalid')
        if body.state == 'CONTAINED' and not db.execute('SELECT 1 FROM run_containment_v6 WHERE run_id=?',
                                                       (incident['run_id'],)).fetchone():
            raise HTTPException(409, 'Run has no effective V6 containment record')
        artifact = db.execute('SELECT uri,sha256 FROM artifacts WHERE id=? AND run_id=?',
                               (body.artifact_id, incident['run_id'])).fetchone()
        try:
            intact = artifact and hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest() == artifact['sha256']
        except OSError:
            intact = False
        if not intact:
            raise HTTPException(409, 'Response evidence is missing, changed or belongs to another Run')
        db.execute('INSERT INTO incident_response_events_v6(candidate_id,run_id,state,artifact_id,artifact_sha256,principal_id,created_at) '
                   'VALUES(?,?,?,?,?,?,?)', (candidate_id, incident['run_id'], body.state, body.artifact_id,
                                           artifact['sha256'], LOCAL_SESSION_PRINCIPAL, core.utcnow()))
    return response_history(candidate_id)
