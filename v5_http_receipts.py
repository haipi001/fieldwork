"""Bridge supervised reviewed HTTP replay artifacts into the existing V5 ledger."""
import hashlib
import json
import os
import stat
from pathlib import Path


KIND = 'http_object_read_v1'
TABLES = {'evidence_v2', 'observations', 'http_exchanges', 'identity_profiles', 'identities'}


def capture_sources(db, candidate_id, binding, *, traditional=False):
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
        if evidence['observation_id']:
            add('observations', evidence['observation_id'])
    if not traditional:
        for identifier in binding['sources']:
            add('http_exchanges', identifier)
        for identifier in (binding['owner_identity_id'], binding['other_identity_id']):
            add('identity_profiles', identifier)
            add('identities', identifier)
    return {'candidate_id': candidate_id, 'candidate_sha256': candidate_fingerprint(candidate),
            'run_id': run['id'], 'scope_snapshot_id': run['scope_snapshot_id'], 'policy_id': run['policy_id'],
            'scope_row_sha256': _sha(scope['rules']), 'policy_row_sha256': _sha(policy['policy']),
            'binding': binding, 'refs': refs, 'producer_kind': 'traditional_http' if traditional else 'guided_http'}


def _evidence_set_current(db, candidate, sources, replay, artifact, traditional):
    import final_core as f
    listed = f.load(candidate['evidence_ids'], [])
    if not isinstance(listed, list) or any(not isinstance(identifier, str) for identifier in listed) or len(listed) != len(set(listed)):
        return False
    expected = {ref['id'] for ref in sources.get('refs', []) if ref.get('table') == 'evidence_v2'}
    if not traditional:
        identifier = replay.get('result_evidence_id')
        # Historical guided artifacts lack the marker. Accept only their unique
        # generated replay evidence, never an arbitrary additional reference.
        rows = db.execute("SELECT e.* FROM evidence_v2 e JOIN observations o ON o.id=e.observation_id WHERE e.run_id=? AND e.evidence_type='http.replay' AND e.artifact_id=? AND o.raw_ref=? AND o.observation_type='http.replay_result'",
                          (candidate['run_id'], artifact['id'], artifact['id'])).fetchall()
        rows = [row for row in rows if row['summary'] == '自动执行两轮对象读取正反对照'
                and row['polarity'] == ('supporting' if replay.get('reproduced') and replay.get('stable') else 'counterevidence')]
        if len(rows) != 1 or (identifier and rows[0]['id'] != identifier):
            return False
        expected.add(rows[0]['id'])
    return set(listed) == expected


def _counterevidence_current(db, request, attributes):
    from v5_verification import _load, _sha
    rows = db.execute("SELECT e.*,n.source_type,n.source_ref,n.node_type,n.title,n.body,n.status,n.attributes_json AS node_attributes_json FROM research_edges e JOIN research_nodes n ON n.id=e.source_id WHERE e.target_id=? AND e.relation_type='contradicts' ORDER BY e.id", (request['claim_node_id'],)).fetchall()
    context = []
    for row in rows:
        # The canonical refuted result produces its own receipt-derived counter
        # node after issuance. Only that exact derivation is exempt.
        receipt = db.execute("SELECT r.result_json FROM verification_receipts_v5 r JOIN verification_receipt_bindings_v5 b ON b.receipt_id=r.id WHERE r.id=? AND b.request_id=?", (row['source_ref'], request['id'])).fetchone()
        result = _load(receipt['result_json'], {}) if receipt else {}
        if (receipt and result.get('status') == 'refuted' and row['source_type'] == 'http_counterevidence'
                and row['node_type'] == 'counterevidence' and row['status'] == 'recorded'
                and row['title'] == 'Object-read claim refuted by observed controls'
                and row['body'] == result.get('summary')
                and _load(row['node_attributes_json'], {}) == {'verification_receipt_id': row['source_ref']}
                and _load(row['attributes_json'], {}) == {}):
            continue
        context.append(dict(row))
    return _sha(context) == attributes.get('counterevidence_sha256', _sha([]))


def replay_input(db, request):
    import final_core as f
    import traditional_runtime as http
    from verification_receipts import candidate_fingerprint
    from http_authorization_policy import select_rule, digest
    from v5_verification import _load, _sha
    contract = _load(request['replay_contract_json'], {})
    traditional = 'verification_job_id' in contract
    job_key = 'verification_job_id' if traditional else 'guided_job_id'
    if set(contract) != {'type', 'candidate_id', 'artifact_id', job_key} or contract.get('type') != KIND:
        return None
    candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (contract['candidate_id'],)).fetchone()
    table = 'verification_jobs' if traditional else 'guided_research_jobs'
    job = db.execute(f'SELECT * FROM {table} WHERE id=?', (contract[job_key],)).fetchone()
    campaign = db.execute('SELECT engagement_id,status FROM research_campaigns WHERE id=?', (request['campaign_id'],)).fetchone()
    if (not candidate or not job or not campaign or campaign['status'] != 'active'
            or candidate['status'] in {'archived', 'graveyard'} or campaign['engagement_id'] != candidate['engagement_id']
            or job['run_id'] != candidate['run_id'] or job['cancel_requested']
            or job['status'] not in ({'running', 'completed', 'failed', 'interrupted'} if traditional else {'running', 'completed', 'awaiting_input'})):
        return None
    recorded = _load(job['result'], {})
    if traditional:
        checkpoint = recorded.get('replay_checkpoint', {})
        if (job['candidate_id'] != candidate['id'] or job['oracle'] != 'http-authorization-read-v2'
                or job['completed_requests'] != 10 or job['total_requests'] != 10
                or checkpoint.get('state') not in {'responses_complete', 'failed'} or checkpoint.get('completed_responses') != 10
                or checkpoint.get('final_artifact_id') != contract['artifact_id']):
            return None
    elif ((recorded.get('reviewed_execution') is not True or recorded.get('requests_sent') != 10
            or recorded.get('verification_executed') is not True) or not any(
        item.get('candidate_id') == candidate['id'] and item.get('auto_verification', {}).get('artifact_id') == contract['artifact_id']
        for item in recorded.get('items', [])
    )):
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
    if traditional and sources.get('producer_kind') != 'traditional_http':
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
    if not _evidence_set_current(db, candidate, sources, replay, artifact, traditional):
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
    if (attributes.get('claim_kind') != 'http_object_read' or attributes.get('verification_contract_sha256') != _sha(contract)
            or not _counterevidence_current(db, request, attributes)):
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
                    or proof.get('scope') != 'transport_only' or proof.get('network_grant') not in {'loopback_exact_port','single_connected_socket'}
                    or (proof.get('network_grant') == 'single_connected_socket' and proof.get('network_connect_denied') is not True)
                    or not isinstance(proof.get('child_pid'), int) or proof['child_pid'] == proof.get('parent_pid')):
                return None
            safe[role] = {key: response.get(key) for key in ('status', 'body_sha256', 'body_bytes', 'scalar_sha256')}
        safe_rounds.append(safe)
    return {'type': KIND, 'rounds': safe_rounds, 'principal_field': binding.get('principal_field'),
            'owner_field': binding.get('owner_field'), 'source_artifact_sha256': artifact['sha256'],
            'scope_sha256': digest(scope), 'rule_sha256': digest(rule),
            'rule': {'access': rule['access'], 'allowed_principal_sha256': [digest(p) for p in rule.get('allowed_principals', [])]}}


def request_for_job(job_id, candidate_id, artifact_id, *, traditional=False):
    import final_core as f
    import v5_graph as graph
    import v5_verification as verifier
    job_key = 'verification_job_id' if traditional else 'guided_job_id'
    contract = {'type': KIND, 'candidate_id': candidate_id, 'artifact_id': artifact_id, job_key: job_id}
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
                'verification_contract_sha256': verifier._sha(contract), 'counterevidence_sha256': verifier._sha([]), 'producer_runner_ref': 'traditional-http-supervisor' if traditional else 'guided-http-supervisor'})
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


def finding_plan(receipt_id):
    import final_core as f
    import v5_verification as verifier
    with f.connect() as db:
        row, payload, _ = verifier._validated_receipt(db, receipt_id)
        verifier.validate_receipt_for_promotion(db, receipt_id, row['campaign_id'], row['claim_node_id'], 'verified', KIND)
        contract = payload['replay_contract']
        candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (contract['candidate_id'],)).fetchone()
        current_input = replay_input(db, db.execute('SELECT * FROM verification_requests_v5 WHERE id=(SELECT request_id FROM verification_receipt_bindings_v5 WHERE receipt_id=?)', (receipt_id,)).fetchone())
        quantity = current_input['rounds'][0]['attack']['body_bytes']
        proof = f.VerificationInput(oracle='http-authorization-read-v2', attempts=2, reproduced=True,
            counterevidence_checked=True, counterevidence_summary='Both rounds authenticated distinct principals, bound the object to its owner, and denied the anonymous control. The frozen business rule denied the other principal.',
            severity='unknown', impact_description=f'The selected non-owner read {quantity} bytes of the same owner-bound object in both observed rounds, contrary to the frozen business rule. Broader data access and severity are unproven.',
            steps=['Review the frozen exact object rule and two authorized identities', 'Run owner, non-owner, anonymous and identity GET checks twice in isolated transport workers', 'Independently evaluate the observed scalar hashes in a network-denied process'],
            expected='The selected non-owner and anonymous identity cannot read this object under the frozen rule',
            actual=f'Two owner and non-owner responses were identical; each non-owner response contained {quantity} bytes; anonymous responses were denied. Observation receipt: {receipt_id}.',
            root_cause='Observed object-read authorization enforcement did not satisfy the frozen rule; source implementation has not been examined.',
            weakness='CWE-639', location=candidate['target'], poc_artifact_ids=[contract['artifact_id']])
        fingerprint = verifier._sha({'receipt_sha256': row['receipt_sha256'], 'proof': proof.model_dump()})
        return {'receipt_id': receipt_id, 'candidate_id': candidate['id'], 'source_fingerprint': fingerprint,
                'sends_requests': False, 'severity': 'unknown', 'impact_description': proof.impact_description,
                'root_cause': proof.root_cause, 'proof': proof.model_dump()}


def promote_finding(receipt_id, source_fingerprint):
    import final_core as f
    from fastapi import HTTPException
    from verification_receipts import issue_receipt
    plan = finding_plan(receipt_id)
    if source_fingerprint != plan['source_fingerprint']:
        raise HTTPException(409, '正式结果计划已变化，请重新核对')
    with f.connect() as db:
        existing = db.execute('SELECT * FROM canonical_findings WHERE candidate_id=?', (plan['candidate_id'],)).fetchone()
        if existing:
            verification = f.load(existing['verification'], {})
            machine = db.execute('SELECT result FROM verification_attempts WHERE id=?', (verification.get('receipt_id'),)).fetchone()
            if machine and f.load(machine['result'], {}).get('v5_verification_receipt_id') == receipt_id:
                return f.get_finding(existing['id'])
            raise HTTPException(409, '候选已有其他正式结果，不能用本回执覆盖')
    proof = f.VerificationInput.model_validate(plan['proof'])
    proof.receipt_id = issue_receipt(plan['candidate_id'], proof, v5_receipt_id=receipt_id)
    return f.verify_candidate(plan['candidate_id'], proof)


def fixed_plan(receipt_id, *, db=None):
    import final_core as f
    import v5_verification as v
    from fastapi import HTTPException
    if db is None:
        with f.connect() as connection:
            return fixed_plan(receipt_id, db=connection)
    row, payload, _ = v._validated_receipt(db, receipt_id)
    v.validate_receipt_for_promotion(db, receipt_id, row['campaign_id'], row['claim_node_id'], 'refuted', KIND)
    if payload['result']['classification'] != 'repaired_negative':
        raise HTTPException(409, '修复确认需要两轮实际拒绝旧攻击，合法共享不能作为修复')
    contract = payload['replay_contract']
    candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (contract['candidate_id'],)).fetchone()
    retest = next((r for r in db.execute("SELECT * FROM finding_retests WHERE run_id=? AND status IN ('planned','fixed')", (candidate['run_id'],))
                   if f.load(r['result'], {}).get('candidate_id') == candidate['id']), None)
    if not retest:
        raise HTTPException(409, '回执没有与旧Finding绑定的定向复测计划')
    finding = db.execute('SELECT * FROM canonical_findings WHERE id=?', (retest['finding_id'],)).fetchone()
    if not finding or finding['status'] != 'verified' or finding['target'] != candidate['target'] or finding['category'] != candidate['category'] or finding['engagement_id'] != candidate['engagement_id']:
        raise HTTPException(409, '修复候选不能替换旧Finding的对象、类别或项目')
    old_machine = db.execute("SELECT result FROM verification_attempts WHERE id=? AND status='machine_receipt'", (f.load(finding['verification'], {})['receipt_id'],)).fetchone()
    old_id = f.load(old_machine['result'], {}).get('v5_verification_receipt_id') if old_machine else None
    if not old_id:
        raise HTTPException(409, '旧Finding没有独立V5证明谱系')
    _, old, _ = v._validated_receipt(db, old_id)
    if not v._process_observed(old) or old['result']['status'] != 'verified' or old['replay_contract'].get('type') != KIND:
        raise HTTPException(409, '旧Finding证明不满足独立对象读取协议')
    if payload['created_at'] <= old['created_at']:
        raise HTTPException(409, '修复回执早于最近的违规证明，不能关闭新的重现')
    before, after = old['result']['oracle'], payload['result']['oracle']
    if any(before.get(key) != after.get(key) for key in ('owner_sha256','principal_sha256','rule_sha256')):
        raise HTTPException(409, '修复复测必须核对旧所有者、读取主体和业务权限规则')
    return {'receipt_id': receipt_id, 'finding_id': finding['id'], 'candidate_id': candidate['id'],
            'artifact_id': contract['artifact_id'], 'sends_requests': False,
            'source_fingerprint': v._sha({'receipt_sha256': row['receipt_sha256'], 'retest_id': retest['id'],
                                        'finding_verification': finding['verification'], 'old_receipt_sha256': v._sha(old)})}


def confirm_fixed(receipt_id, source_fingerprint):
    import final_core as f
    from fastapi import HTTPException
    from verification_receipts import issue_fixed_receipt
    plan = fixed_plan(receipt_id)
    if plan['source_fingerprint'] != source_fingerprint:
        raise HTTPException(409, '修复确认计划已变化，请重新核对')
    with f.connect() as db:
        stored = db.execute("SELECT * FROM verification_attempts WHERE candidate_id=? AND status='machine_negative_receipt'", (plan['candidate_id'],)).fetchall()
        for row in stored:
            if f.load(row['result'], {}).get('v5_verification_receipt_id') == receipt_id:
                return {'id': plan['finding_id'], 'candidate_id': plan['candidate_id'], 'status':'verified_fixed', 'receipt_id':row['id']}
        artifact = db.execute('SELECT uri FROM artifacts WHERE id=?', (plan['artifact_id'],)).fetchone()
    replay = json.loads(Path(artifact['uri']).read_text())
    return issue_fixed_receipt(plan['candidate_id'], 'http-authorization-read-v2', plan['artifact_id'], replay['repair_checks'], v5_receipt_id=receipt_id)
