"""Server-side proof receipts. No HTTP endpoint can issue these records."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def candidate_fingerprint(candidate):
    return _digest({key: candidate[key] for key in ('id', 'run_id', 'target', 'category', 'title', 'hypothesis')})


def issue_receipt(candidate_id, proof):
    """Called only after a registered runtime has executed and persisted its proof."""
    import final_core as core
    if proof.oracle not in {'http-state-replay-v1', 'http-authorization-read-v2', 'forge-property-replay-v1'} and not proof.oracle.startswith('ptai:'):
        raise HTTPException(409, 'Oracle 尚未注册服务端证明协议')
    if not proof.reproduced or proof.attempts < 2 or not proof.counterevidence_checked or not proof.counterevidence_summary.strip():
        raise HTTPException(409, '不完整复验不能签发成功收据')
    with core.connect() as db:
        candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (candidate_id,)).fetchone()
        if not candidate or candidate['status'] in {'verified', 'archived'}:
            raise HTTPException(409, '候选不可签发复验收据')
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (candidate['run_id'],)).fetchone()
        scope = db.execute('SELECT * FROM scope_snapshots WHERE id=? AND confirmed_at IS NOT NULL', (run['scope_snapshot_id'],)).fetchone()
        if not scope or not proof.poc_artifact_ids:
            raise HTTPException(409, '复验缺少 Scope 或实际 Artifact')
        artifacts = {}
        for artifact_id in set(proof.poc_artifact_ids):
            artifact = db.execute('SELECT * FROM artifacts WHERE id=? AND run_id=?', (artifact_id, run['id'])).fetchone()
            if not artifact:
                raise HTTPException(409, '复验证据不属于当前运行')
            path = Path(artifact['uri'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                raise HTTPException(409, '复验证据缺失或已被修改')
            artifacts[artifact_id] = artifact['sha256']
        program_snapshot = None
        if proof.program_snapshot_id:
            row = db.execute('SELECT * FROM program_snapshots WHERE id=? AND engagement_id=?',
                             (proof.program_snapshot_id, candidate['engagement_id'])).fetchone()
            if not row:
                raise HTTPException(409, '复验引用的 ProgramSnapshot 不属于当前项目')
            program_snapshot = {'id': row['id'], 'rules_sha256': _digest(row['rules']), 'source_uri': row['source_uri']}
        receipt_id = core.uid('receipt')
        payload = {
            'schema': 'verification-receipt/1', 'candidate_fingerprint': candidate_fingerprint(candidate),
            'run_id': run['id'], 'scope_snapshot_id': scope['id'], 'scope_sha256': _digest(scope['rules']),
            'artifacts': artifacts, 'proof': proof.model_dump(exclude={'receipt_id'}),
            'program_snapshot': program_snapshot,
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        }
        db.execute('INSERT INTO verification_attempts VALUES(?,?,?,?,?,?,?,?)', (
            receipt_id, candidate_id, proof.oracle, 'machine_receipt', proof.attempts,
            core.dump(payload), core.utcnow(), core.utcnow()))
    return receipt_id


def validate_receipt(db, candidate, run, scope, proof):
    """Resolve proof from stored execution, never from a caller's success claims."""
    import final_core as core
    receipt = db.execute("SELECT * FROM verification_attempts WHERE id=? AND candidate_id=? AND status='machine_receipt'",
                         (proof.receipt_id, candidate['id'])).fetchone() if proof.receipt_id else None
    if not receipt:
        raise HTTPException(409, '需要服务端真实复验收据；填写已复现字段不能生成 Verified Finding')
    payload = core.load(receipt['result'], {})
    if (payload.get('schema') != 'verification-receipt/1' or payload.get('run_id') != run['id']
            or payload.get('candidate_fingerprint') != candidate_fingerprint(candidate)
            or payload.get('scope_snapshot_id') != scope['id'] or payload.get('scope_sha256') != _digest(scope['rules'])):
        raise HTTPException(409, '收据与当前候选或授权范围不一致，请重新复验')
    if datetime.fromisoformat(payload['expires_at']) <= datetime.now(timezone.utc):
        raise HTTPException(409, '复验收据已过期，请重新复验')
    if payload.get('proof') != proof.model_dump(exclude={'receipt_id'}):
        raise HTTPException(409, '提交内容与服务端复验结果不一致')
    if payload.get('program_snapshot'):
        program = db.execute('SELECT * FROM program_snapshots WHERE id=? AND engagement_id=?',
                             (payload['program_snapshot']['id'], candidate['engagement_id'])).fetchone()
        if not program or _digest(program['rules']) != payload['program_snapshot']['rules_sha256']:
            raise HTTPException(409, '项目规则快照缺失或与收据不一致')
    if not payload.get('artifacts'):
        raise HTTPException(409, '收据缺少实际证据')
    for artifact_id, expected in payload['artifacts'].items():
        artifact = db.execute('SELECT * FROM artifacts WHERE id=? AND run_id=?', (artifact_id, run['id'])).fetchone()
        if not artifact or artifact['sha256'] != expected:
            raise HTTPException(409, '收据引用了不存在或不匹配的 Artifact')
        path = Path(artifact['uri'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise HTTPException(409, '复验证据缺失或哈希校验失败')
    return receipt['id']


def issue_fixed_receipt(candidate_id, oracle, artifact_id, repair_checks):
    """Close a planned retest only after a registered runtime proves the old exploit is denied twice."""
    import final_core as core
    if oracle != 'http-authorization-read-v2' or len(repair_checks) < 2 or not all(item.get('passed') is True for item in repair_checks):
        raise HTTPException(409, '负向复测未满足已注册的修复确认协议')
    with core.connect() as db:
        candidate = db.execute('SELECT * FROM candidate_findings WHERE id=?', (candidate_id,)).fetchone()
        if not candidate or candidate['status'] != 'candidate':
            raise HTTPException(409, '修复复测候选不存在或状态无效')
        retest = next((row for row in db.execute(
            "SELECT * FROM finding_retests WHERE run_id=? AND status='planned' ORDER BY created_at DESC",
            (candidate['run_id'],),
        ).fetchall() if core.load(row['result'], {}).get('candidate_id') == candidate_id), None)
        if not retest:
            raise HTTPException(409, '没有与当前候选绑定的修复复测计划')
        lifecycle = db.execute(
            "SELECT * FROM finding_lifecycle WHERE finding_id=? AND status IN ('fix_claimed','retest_required')",
            (retest['finding_id'],),
        ).fetchone()
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (candidate['run_id'],)).fetchone()
        scope = db.execute(
            'SELECT * FROM scope_snapshots WHERE id=? AND confirmed_at IS NOT NULL', (run['scope_snapshot_id'],),
        ).fetchone() if run else None
        artifact = db.execute(
            'SELECT * FROM artifacts WHERE id=? AND run_id=?', (artifact_id, candidate['run_id']),
        ).fetchone()
        if not lifecycle or not run or not scope or not artifact:
            raise HTTPException(409, '修复复测缺少生命周期、Scope 或实际 Artifact')
        path = Path(artifact['uri'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if not digest or digest != artifact['sha256']:
            raise HTTPException(409, '修复复测 Artifact 缺失或哈希不一致')
        receipt_id, timestamp = core.uid('receipt-fixed'), core.utcnow()
        payload = {
            'schema': 'fix-verification-receipt/1', 'finding_id': retest['finding_id'],
            'finding_fingerprint': lifecycle['fingerprint'], 'candidate_fingerprint': candidate_fingerprint(candidate),
            'run_id': run['id'], 'scope_snapshot_id': scope['id'], 'scope_sha256': _digest(scope['rules']),
            'oracle': oracle, 'artifact_id': artifact_id, 'artifact_sha256': digest,
            'repair_checks': repair_checks,
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        }
        db.execute('INSERT INTO verification_attempts VALUES(?,?,?,?,?,?,?,?)', (
            receipt_id, candidate_id, oracle, 'machine_negative_receipt', len(repair_checks),
            core.dump(payload), timestamp, timestamp,
        ))
        history = core.load(lifecycle['history'], [])
        history.append({'at': timestamp, 'kind': 'verified_fixed', 'run_id': run['id'],
                        'candidate_id': candidate_id, 'receipt_id': receipt_id})
        db.execute("UPDATE finding_lifecycle SET status='verified_fixed',last_seen_run_id=?,history=?,updated_at=? WHERE finding_id=?", (
            run['id'], core.dump(history), timestamp, retest['finding_id'],
        ))
        db.execute("UPDATE finding_retests SET status='fixed',result=?,completed_at=? WHERE id=?", (
            core.dump({'candidate_id': candidate_id, 'receipt_id': receipt_id, 'artifact_id': artifact_id}),
            timestamp, retest['id'],
        ))
        db.execute("UPDATE candidate_findings SET status='verified_fixed',updated_at=? WHERE id=?", (timestamp, candidate_id))
    return {'id': retest['finding_id'], 'candidate_id': candidate_id, 'status': 'verified_fixed',
            'receipt_id': receipt_id, 'lifecycle_status': 'verified_fixed'}
