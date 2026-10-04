"""Immutable, non-promotable checkpoints of observed HTTP replay responses."""
import hashlib
import json
import os


def persist(run, candidate_id, rounds, required_requests, *, previous=None, state='in_progress', error_type=None, final_artifact_id=None):
    import final_core as f
    import traditional_runtime as http
    safe = [{role: {key: response.get(key) for key in
                   ('status', 'body_sha256', 'body_bytes', 'scalar_sha256', 'process_execution')
                   if key in response}
             for role, response in group.items()} for group in rounds]
    count = sum(len(group) for group in safe)
    payload = {'schema': 'http-replay-checkpoint/1', 'run_id': run['id'], 'candidate_id': candidate_id,
               'scope_snapshot_id': run['scope_snapshot_id'], 'policy_id': run['policy_id'],
               'state': state, 'completed_responses': count, 'required_requests': required_requests,
               'rounds': safe, 'verification_complete': False, 'promotion_eligible': False,
               'previous_artifact_id': previous, 'error_type': error_type,
               'final_artifact_id': final_artifact_id}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    identifier = f.uid('artifact')
    http.ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    path = http.ARTIFACT_ROOT / f'{identifier}.json'
    # Unique immutable file: a crash cannot invalidate the previous checkpoint.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    sha = hashlib.sha256(encoded).hexdigest()
    with f.connect() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                   (identifier, run['id'], 'http.replay.checkpoint', str(path), sha, 'application/json', 1, f.utcnow()))
        observation_id = f.uid('obs')
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (observation_id, run['id'], run['engagement_id'], 'traditional', 'http.replay_checkpoint',
                    candidate_id, f'HTTP replay checkpoint: {count}/{required_requests} responses; {state}; not proof',
                    0.0, 'http-replay-checkpoint', identifier, f.utcnow()))
    return {'artifact_id': identifier, 'artifact_sha256': sha, 'observation_id': observation_id,
            'completed_responses': count, 'required_requests': required_requests, 'state': state,
            'verification_complete': False, 'promotion_eligible': False, 'final_artifact_id': final_artifact_id}
