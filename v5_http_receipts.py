"""Bridge supervised reviewed HTTP replay artifacts into the existing V5 ledger."""
import hashlib
import json
import os
import stat
from pathlib import Path


KIND = 'http_object_read_v1'
TABLES = {'evidence_v2', 'observations', 'http_exchanges', 'identity_profiles', 'identities'}


def capture_sources(db, candidate_id, binding):
    import final_core as f
    from verification_receipts import candidate_fingerprint
    from v5_verification import _sha
    candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (candidate_id,)).fetchone()
    run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (candidate['run_id'],)).fetchone()
    scope = db.execute('SELECT rules FROM scope_snapshots WHERE id=?', (run['scope_snapshot_id'],)).fetchone()
    policy = db.execute('SELECT policy FROM execution_policies WHERE id=?', (run['policy_id'],)).fetchone()
    refs = []
    def add(table, identifier):
        key = 'identity_id' if table == 'identity_profiles' else 'id'
        row = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('HTTP source is missing')
        refs.append({'table': table, 'id': identifier, 'sha256': _sha(dict(row))})
        return row
    for identifier in f.load(candidate['evidence_ids'], []):
        evidence = add('evidence_v2', identifier)
        add('observations', evidence['observation_id'])
    for identifier in binding['sources']:
        add('http_exchanges', identifier)
    for identifier in (binding['owner_identity_id'], binding['other_identity_id']):
        add('identity_profiles', identifier)
        add('identities', identifier)
    return {'candidate_id': candidate_id, 'candidate_sha256': candidate_fingerprint(candidate),
            'run_id': run['id'], 'scope_snapshot_id': run['scope_snapshot_id'], 'policy_id': run['policy_id'],
            'scope_row_sha256': _sha(scope['rules']), 'policy_row_sha256': _sha(policy['policy']),
            'binding': binding, 'refs': refs}


def replay_input(db, request):
    import final_core as f
    import traditional_runtime as http
    from verification_receipts import candidate_fingerprint
    from http_authorization_policy import select_rule, digest
    from v5_verification import _load, _sha
    contract = _load(request['replay_contract_json'], {})
    if set(contract) != {'type', 'candidate_id', 'artifact_id', 'guided_job_id'} or contract.get('type') != KIND:
        return None
    candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (contract['candidate_id'],)).fetchone()
    job = db.execute('SELECT * FROM guided_research_jobs WHERE id=?', (contract['guided_job_id'],)).fetchone()
    campaign = db.execute('SELECT engagement_id,status FROM research_campaigns WHERE id=?', (request['campaign_id'],)).fetchone()
    if (not candidate or not job or not campaign or campaign['status'] != 'active'
            or candidate['status'] in {'archived', 'graveyard'} or campaign['engagement_id'] != candidate['engagement_id']
            or job['run_id'] != candidate['run_id'] or job['cancel_requested']
            or job['status'] not in {'running', 'completed', 'awaiting_input'}):
        return None
    recorded = _load(job['result'], {})
    if (recorded.get('reviewed_execution') is not True or recorded.get('requests_sent') != 10
            or recorded.get('verification_executed') is not True) or not any(
        item.get('candidate_id') == candidate['id'] and item.get('auto_verification', {}).get('artifact_id') == contract['artifact_id']
        for item in recorded.get('items', [])
    ):
        return None
    artifact = db.execute("SELECT * FROM artifacts WHERE id=? AND run_id=? AND kind='http.replay'", (contract['artifact_id'], candidate['run_id'])).fetchone()
    if not artifact:
        return None
    input_ids = _load(request['input_node_ids_json'], [])
    artifact_nodes = db.execute("SELECT id,attributes_json FROM research_nodes WHERE campaign_id=? AND node_type='artifact' AND source_type='http.replay' AND source_ref=?",
                                (request['campaign_id'], artifact['id'])).fetchall()
    if not any(node['id'] in input_ids and _load(node['attributes_json'], {}).get('artifact_sha256') == artifact['sha256'] for node in artifact_nodes):
        return None
    path = Path(artifact['uri'])
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(http.ARTIFACT_ROOT.resolve()) or path.stat().st_size > 128000:
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 128000:
                return None
            raw = stream.read(128001)
        if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
            return None
        replay = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(replay, dict) or len(raw) > 128000:
        return None
    sources = replay.get('source_snapshot', {})
    if not isinstance(sources, dict):
        return None
    if (sources.get('candidate_id') != candidate['id'] or sources.get('candidate_sha256') != candidate_fingerprint(candidate)
            or sources.get('run_id') != candidate['run_id']):
        return None
    engagement = db.execute('SELECT * FROM engagements_v2 WHERE id=?', (candidate['engagement_id'],)).fetchone()
    run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (candidate['run_id'],)).fetchone()
    if (not run or not engagement or engagement['status'] == 'archived'
            or run['status'] not in {'running', 'paused', 'completed'}
            or sources.get('scope_snapshot_id') != run['scope_snapshot_id']
            or run['scope_snapshot_id'] != engagement['current_scope_snapshot_id']
            or sources.get('policy_id') != run['policy_id'] or run['policy_id'] != engagement['current_policy_id']):
        return None
    scope_row = db.execute('SELECT rules,confirmed_at FROM scope_snapshots WHERE id=?', (run['scope_snapshot_id'],)).fetchone()
    policy_row = db.execute('SELECT policy FROM execution_policies WHERE id=?', (run['policy_id'],)).fetchone()
    if (not scope_row or not policy_row or not scope_row['confirmed_at']
            or sources.get('scope_row_sha256') != _sha(scope_row['rules'])
            or sources.get('policy_row_sha256') != _sha(policy_row['policy'])):
        return None
    scope = f.load(scope_row['rules'], {})
    rule, _ = select_rule(scope, candidate['target'])
    if not rule or not scope.get('allow_authentication'):
        return None
    refs = sources.get('refs', [])
    if not isinstance(refs, list) or not refs or len(refs) > 300:
        return None
    evidence_ids = set(f.load(candidate['evidence_ids'], []))
    for ref in refs:
        if not isinstance(ref, dict) or ref.get('table') not in TABLES:
            return None
        key = 'identity_id' if ref['table'] == 'identity_profiles' else 'id'
        row = db.execute(f"SELECT * FROM {ref['table']} WHERE {key}=?", (ref['id'],)).fetchone()
        if not row or _sha(dict(row)) != ref.get('sha256'):
            return None
        if ref['table'] == 'identity_profiles':
            from guided_http import ready
            if not ready(dict(row)):
                return None
        if ref['table'] == 'evidence_v2' and ref['id'] not in evidence_ids:
            return None
    claim = db.execute('SELECT attributes_json FROM research_nodes WHERE id=?', (request['claim_node_id'],)).fetchone()
    attributes = _load(claim['attributes_json'], {}) if claim else {}
    if attributes.get('claim_kind') != 'http_object_read' or attributes.get('verification_contract_sha256') != _sha(contract):
        return None
    binding = sources.get('binding', {})
    rounds = replay.get('rounds', [])
    if len(rounds) != 2:
        return None
    safe_rounds = []
    for group in rounds:
        if set(group) != {'baseline', 'attack', 'negative_control', 'baseline_identity', 'attack_identity'}:
            return None
        safe = {}
        for role, response in group.items():
            proof = response.get('process_execution', {})
            if (proof.get('observed_by') != 'fieldwork_local_supervisor' or proof.get('file_read_denied') is not True
                    or proof.get('exit_code') != 0 or proof.get('sandbox') != 'macos-seatbelt'
                    or proof.get('scope') != 'transport_only' or proof.get('network_grant') != 'loopback_exact_port'
                    or not isinstance(proof.get('child_pid'), int) or proof['child_pid'] == proof.get('parent_pid')):
                return None
            safe[role] = {key: response.get(key) for key in ('status', 'body_sha256', 'body_bytes', 'scalar_sha256')}
        safe_rounds.append(safe)
    return {'type': KIND, 'rounds': safe_rounds, 'principal_field': binding.get('principal_field'),
            'owner_field': binding.get('owner_field'), 'source_artifact_sha256': artifact['sha256'],
            'scope_sha256': digest(scope), 'rule_sha256': digest(rule),
            'rule': {'access': rule['access'], 'allowed_principal_sha256': [digest(p) for p in rule.get('allowed_principals', [])]}}


def request_for_job(job_id, candidate_id, artifact_id):
    import final_core as f
    import v5_graph as graph
    import v5_verification as verifier
    contract = {'type': KIND, 'candidate_id': candidate_id, 'artifact_id': artifact_id, 'guided_job_id': job_id}
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (candidate_id,)).fetchone()
        campaign = db.execute("SELECT id FROM research_campaigns WHERE engagement_id=? AND status='active' ORDER BY created_at LIMIT 1", (candidate['engagement_id'],)).fetchone()
        if not campaign:
            identifier, now = f.uid('campaign'), f.utcnow()
            db.execute('INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identifier, candidate['engagement_id'], 'Reviewed HTTP verification', 'Independently check reviewed object reads and frozen business rules', 'active', 'business_logic', 20, 0, 30, .85, now, now))
            campaign_id = identifier
        else:
            campaign_id = campaign['id']
        artifact = db.execute('SELECT sha256 FROM artifacts WHERE id=? AND run_id=?', (artifact_id, candidate['run_id'])).fetchone()
        if not artifact:
            raise ValueError('HTTP artifact is missing')
        evidence_id, _ = graph._bridge_node(db, campaign_id, source_type='http.replay', source_ref=artifact_id,
            node_type='artifact', title='Supervised two-round HTTP replay', body='Source-ref only; original redacted artifact retained.',
            status='recorded', run_id=candidate['run_id'], attributes={'artifact_id': artifact_id, 'artifact_sha256': artifact['sha256']})
        claim_id, _ = graph._bridge_node(db, campaign_id, source_type='reviewed_http_claim', source_ref=artifact_id,
            node_type='claim', title='Selected object read violates the frozen business rule', body='Bounded claim; severity and code root cause remain unproven.',
            status='active', run_id=candidate['run_id'], attributes={'claim_kind': 'http_object_read', 'candidate_id': candidate_id,
                'verification_contract_sha256': verifier._sha(contract), 'producer_runner_ref': 'guided-http-supervisor'})
        graph._bridge_edge(db, campaign_id, claim_id, evidence_id, 'tested_by')
        claim = db.execute('SELECT * FROM research_nodes WHERE id=?', (claim_id,)).fetchone()
        request, task, _ = verifier._create_request_transaction(db, claim, verifier.VerifyRequest(
            campaign_id=campaign_id, evidence_ids=[evidence_id], replay_contract=contract))
        if replay_input(db, request) is None:
            raise ValueError('reviewed HTTP inputs are not current')
    return {'request_id': request['id'], 'task_id': task['id'], 'claim_id': claim_id, 'campaign_id': campaign_id}


def canonical_for_receipt(receipt_id):
    import final_core as f
    import v5_graph as graph
    import v5_verification as verifier
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        receipt = verifier._get_receipt_value(db, receipt_id)
        if not receipt['integrity']['promotion_eligible']:
            return None
        outcome = receipt['result']['status']
        verifier.validate_receipt_for_promotion(db, receipt_id, receipt['campaign_id'], receipt['claim_node_id'], outcome, KIND)
        node_id, _ = graph._bridge_node(db, receipt['campaign_id'], source_type=KIND + ':' + receipt_id, source_ref=receipt['claim_node_id'],
            node_type='canonical_result', title='Selected object read: ' + outcome,
            body=receipt['result']['summary'] + '. Severity and code root cause remain unproven.', status=outcome,
            attributes={'verification_receipt_id': receipt_id, 'verification_outcome': outcome,
                        'source_claim_id': receipt['claim_node_id'], 'verification_domain': 'http_object_read',
                        'severity': 'unknown', 'formal_finding_created': False})
        graph._bridge_edge(db, receipt['campaign_id'], receipt['claim_node_id'], node_id, 'verified_by')
        if outcome == 'refuted':
            counter_id, _ = graph._bridge_node(db, receipt['campaign_id'], source_type='http_counterevidence', source_ref=receipt_id,
                node_type='counterevidence', title='Object-read claim refuted by observed controls',
                body=receipt['result']['summary'], status='recorded', attributes={'verification_receipt_id': receipt_id})
            graph._bridge_edge(db, receipt['campaign_id'], counter_id, receipt['claim_node_id'], 'contradicts')
        return node_id
