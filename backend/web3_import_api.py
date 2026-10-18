"""Session-protected routes; durable imports are scoped to existing engagements."""
import asyncio
import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from storage_paths import data_directory
from web3_contract_import import ImportFailure, parse_target, import_contract, private_directory, write_json, digest, encoded, now

router = APIRouter(prefix='/api/v1/web3/contract-imports', tags=['Web3 imports'])
TASKS = {}
CANCEL = {}
ACTIVE = {'importing_source', 'source_ready', 'compile_running', 'compiled', 'alignment_running'}
LOCK = threading.RLock()


class ImportInput(BaseModel):
    engagement_id: str = Field(min_length=1, max_length=160)
    address: str = Field(min_length=1, max_length=1000)
    chain_id: int | None = Field(default=None, ge=1)
    mode: Literal['source', 'build', 'align', 'auto'] = 'build'
    rpc_url: str | None = Field(default=None, max_length=2000)
    rpc_env: str | None = Field(default=None, pattern=r'^[A-Z][A-Z0-9_]{0,99}$')
    refresh: bool = False


class AttachInput(BaseModel):
    engagement_id: str
    run_id: str | None = None


def jobs_root():
    return private_directory(data_directory() / 'web3-imports' / 'jobs')


def job_path(identity):
    if not re.fullmatch(r'import-[0-9a-f]{32}', identity):
        raise HTTPException(404, '导入任务不存在')
    path = jobs_root() / f'{identity}.json'
    if path.is_symlink():
        raise HTTPException(409, '任务文件不安全')
    return path


def scoped(body):
    import final_core
    engagement = final_core.get_engagement(body.engagement_id)
    if engagement['mode'] != 'web3' or engagement.get('target_type') != 'contract':
        raise HTTPException(409, '需要 Web3 合约地址项目')
    try:
        target = parse_target(body.address, body.chain_id or engagement.get('chain_id'))
        approved = parse_target(engagement['normalized_target'], engagement.get('chain_id') or target['chain_id'])
    except ImportFailure as error:
        raise HTTPException(422, error.message)
    if target['address'] != approved['address'] or (engagement.get('chain_id') and target['chain_id'] != engagement['chain_id']):
        raise HTTPException(409, '合约或链不属于当前项目 Scope')
    return engagement, target


def read_job(identity):
    with LOCK:
        path = job_path(identity)
        if not path.is_file():
            raise HTTPException(404, '导入任务不存在')
        value = json.loads(path.read_text())
        scoped(ImportInput(**value['request']))
        if value['status'] in ACTIVE | {'queued'} and not value.get('finished_at') and identity not in TASKS:
            value.update(status='interrupted', error={'code': 'restart_interrupted', 'message': '服务重启中断导入；可重试并复用缓存'})
            write_json(path, value)
        return value


def save_job(identity, value):
    with LOCK:
        write_json(job_path(identity), value)


async def dispatch(identity, body, initial):
    from storage_access import storage_call
    cancel = CANCEL[identity]
    rpc_url = body.rpc_url or (os.environ.get(body.rpc_env) if body.rpc_env else None)
    def work():
        value = copy.deepcopy(initial)
        def progress(result):
            value.update(result)
            value['events'].append({'status': result['status'], 'at': now()})
            save_job(identity, value)
        result = import_contract(body.address, body.chain_id, body.mode, rpc_url, body.refresh, base=data_directory() / "web3-imports", progress=progress, cancelled=cancel.is_set)
        value.update(result)
        if cancel.is_set():
            value['status'] = 'cancelled'
        value['events'].append({'status': value['status'], 'at': now()})
        value['finished_at'] = now()
        save_job(identity, value)
    try:
        await asyncio.to_thread(storage_call, data_directory().resolve(), work)
    except asyncio.CancelledError:
        cancel.set()
        raise
    except Exception:
        value = {**initial, 'status': 'failed', 'error': {'code': 'local_failure', 'message': '导入任务异常，可重试；检查本地权限与依赖'}}
        save_job(identity, value)
    finally:
        with LOCK:
            TASKS.pop(identity, None)
            CANCEL.pop(identity, None)


async def shutdown():
    for event in CANCEL.values():
        event.set()
    if TASKS:
        # Threads keep storage leases until the current bounded operation exits.
        await asyncio.gather(*list(TASKS.values()), return_exceptions=True)


@router.post('', status_code=202)
async def create_import(body: ImportInput):
    _, target = scoped(body)
    if body.mode == 'auto':
        configured = target['chain_id'] == 1 and bool(os.environ.get('ETHEREUM_RPC_URL'))
        body = body.model_copy(update={'mode': 'align' if configured else 'build', 'rpc_env': 'ETHEREUM_RPC_URL' if configured else None})
    if body.rpc_url:
        from web3_contract_import import validate_rpc
        try:
            validate_rpc(body.rpc_url)
        except ImportFailure as error:
            raise HTTPException(422, error.message)
    request = {**body.model_dump(exclude={'rpc_url'}), 'address': target['address'], 'chain_id': target['chain_id']}
    # URL/key never enters an idempotency fingerprint or saved request.
    identity = 'import-' + digest(encoded({**request, 'refresh': False}))[:32]
    with LOCK:
        path = job_path(identity)
        if identity in TASKS:
            return read_job(identity)
        if path.exists() and not body.refresh and body.mode != 'align':
            old = read_job(identity)
            if old.get('status') not in {'failed', 'interrupted', 'cancelled', 'compile_failed', 'unverified_source', 'alignment_blocked', 'chain_unchecked'}:
                return old
        if len(TASKS) >= 2:
            raise HTTPException(429, '最多同时处理两个源码导入任务')
        initial = {'import_id': identity, 'request': request, **target, 'status': 'queued', 'created_at': now(), 'events': [{'status': 'queued', 'at': now()}]}
        save_job(identity, initial)
        CANCEL[identity] = threading.Event()
        TASKS[identity] = asyncio.create_task(dispatch(identity, body.model_copy(update={'chain_id': target['chain_id']}), initial))
    return initial


@router.get('/engagements/{engagement_id}/latest')
def latest_import(engagement_id: str):
    import final_core
    final_core.get_engagement(engagement_id)
    candidates = []
    for path in list(jobs_root().glob('import-*.json'))[:1000]:
        if path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text())
            if value.get('request', {}).get('engagement_id') == engagement_id:
                candidates.append(value)
        except (OSError, ValueError):
            continue
    if not candidates:
        return {'import': None}
    selected = max(candidates, key=lambda v: v.get('created_at', ''))
    return {'import': read_job(selected['import_id'])}


@router.get('/{import_id}')
def get_import(import_id: str):
    return read_job(import_id)


@router.post('/{import_id}/retry', status_code=202)
async def retry_import(import_id: str, body: ImportInput):
    value = read_job(import_id)
    if value['request']['engagement_id'] != body.engagement_id:
        raise HTTPException(409, '不能跨项目重试导入')
    if import_id in TASKS:
        return value
    return await create_import(body)


@router.post('/{import_id}/cancel')
def cancel_import(import_id: str):
    value = read_job(import_id)
    if import_id in CANCEL:
        CANCEL[import_id].set()
        return {**value, 'cancel_requested': True}
    return value


@router.post('/{import_id}/attach')
def attach_import(import_id: str, body: AttachInput):
    import final_core
    value = read_job(import_id)
    if body.engagement_id != value['request']['engagement_id']:
        raise HTTPException(409, '不能跨项目绑定导入产物')
    if not value.get('source_path') or value['status'] not in {'source_ready', 'chain_unchecked', 'aligned', 'alignment_blocked', 'compile_failed', 'ready_for_research'}:
        raise HTTPException(409, '导入材料尚不可用')
    if body.run_id:
        with final_core.connect() as db:
            if not db.execute('SELECT id FROM analysis_runs WHERE id=? AND engagement_id=?', (body.run_id, body.engagement_id)).fetchone():
                raise HTTPException(409, 'Run 不属于当前项目')
    snapshot_id = value.get('program_snapshot_id')
    if value.get('alignment') and not snapshot_id:
        alignment = value['alignment']
        rules = {'kind': 'deployment_alignment', **alignment, 'status': 'aligned' if alignment['status'] == 'aligned' else 'blocked',
                 'runtime_bytecode_match': alignment['match']['status'] == 'exact_match',
                 'import_id': import_id, 'fingerprint': value['fingerprint']}
        snapshot = final_core.create_program_snapshot(final_core.ProgramSnapshotInput(engagement_id=body.engagement_id, platform='immunefi', source_uri=value['source_path'], rules=rules))
        snapshot_id = snapshot['id']
        value['program_snapshot_id'] = snapshot_id
    value['attachment'] = {'engagement_id': body.engagement_id, 'run_id': body.run_id, 'at': now()}
    if value.get('alignment', {}).get('status') == 'aligned':
        value['status'] = 'ready_for_research'
    save_job(import_id, value)
    return {'import_id': import_id, 'source_path': value['source_path'], 'status': value['status'], 'program_snapshot_id': snapshot_id,
            'chain_id': value['chain_id'], 'fork_block_number': value.get('alignment', {}).get('block_number'), 'authorization_granted': False}


def read_only_materials(engagement_id, import_id):
    value = read_job(import_id)
    if value['request']['engagement_id'] != engagement_id or not value.get('finished_at'):
        raise HTTPException(409, '只读研究必须绑定本项目已完成的导入资料')
    allowed = {'source_ready', 'chain_unchecked', 'aligned', 'alignment_blocked', 'compile_failed', 'ready_for_research'}
    if value.get('status') not in allowed or not isinstance(value.get('source_path'), str):
        raise HTTPException(409, '导入未形成可用源码，不能启动只读研究；请重试导入')
    source = Path(value['source_path'])
    approved = data_directory() / 'web3-imports'
    if (not source.is_absolute() or '..' in source.parts or source.is_symlink()
            or not source.is_dir() or not source.resolve().is_relative_to(approved.resolve())):
        raise HTTPException(409, '源码缺失或不在批准的导入目录，请重新导入')
    try:
        from web3_materials import verified_sources
        verified_sources(source, value.get('fingerprint'))
    except Exception as error:
        raise HTTPException(409, '导入源码与保存清单不一致，请重新导入') from error
    return value


def execute_read_only(run_id, engagement_id, import_id):
    """Consume existing Run budget and evidence stores; no tests, RPC, or model calls."""
    import final_core
    try:
        value = read_only_materials(engagement_id, import_id)
        with final_core.connect() as db:
            run = db.execute('SELECT status FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
            if not run or run['status'] in {'stopped', 'paused'}:
                return
            db.execute("UPDATE analysis_runs SET status='running',started_at=?,current_stage='analysis' WHERE id=?", (now(), run_id))
        artifact_id = final_core.uid('artifact')
        root = private_directory(data_directory() / 'artifacts')
        artifact_path = root / f'{artifact_id}.json'
        materials = {k: v for k, v in value.items() if k not in {'request', 'source_path'}}
        materials['verification_level'] = 'read_only_materials'
        if value.get('source_path'):
            source = Path(value['source_path'])
            approved = data_directory() / 'web3-imports'
            if not source.resolve().is_relative_to(approved.resolve()) or source.is_symlink():
                raise HTTPException(409, '源码不在批准的导入目录')
            from web3_analysis import source_model
            from web3_materials import verified_sources
            from web3_contract_import import write_bytes
            import tempfile
            sources = verified_sources(source, value.get('fingerprint'))
            # Consume the verified bytes, avoiding a later reread of mutable imports.
            with tempfile.TemporaryDirectory(prefix='fieldwork-source-analysis-') as directory:
                snapshot = Path(directory)
                for name, entry in sources.items():
                    target = snapshot / 'sources' / name
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    write_bytes(target, entry['content'].encode())
                materials['source_analysis'] = source_model(snapshot)
        else:
            materials['source_analysis'] = {'status': 'unavailable', 'reason': 'source_unavailable'}
        write_json(artifact_path, materials)
        with final_core.connect() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (artifact_id, run_id, 'web3.contract_import', str(artifact_path), digest(artifact_path.read_bytes()), 'application/json', 1, now()))
        final_core.record_observation(run_id, final_core.ObservationInput(
            observation_type='contract_import', subject=value['address'], source_capability='web3-public-source-import',
            summary=f"只读材料：{value.get('contract_name', 'unknown')}；来源 {value.get('provider', 'unavailable')}；源码文件 {value.get('file_count', 0)}；编译 {value.get('compile', {}).get('status', 'not_run')}；对齐 {value.get('alignment', {}).get('status', 'unchecked')}。不执行属性测试、Fork 或漏洞晋升。",
            raw_ref=artifact_id, confidence=1.0,
        ))
        final_core.add_event(run_id, 'analysis', 'web3.import.read_only', '公开资料已纳入 Observation、Evidence 和研究图谱', {'import_id': import_id, 'artifact_id': artifact_id, 'verification': 'not_tested', 'fork': 'not_run'})
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='completed',current_stage='report',completed_at=? WHERE id=? AND status='running'", (now(), run_id))
        final_core.add_event(run_id, 'report', 'run.completed', '只读研究完成；漏洞验证与部署归因尚未覆盖', {'synthetic': False, 'candidate_count': 0})
    except Exception:
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='failed',completed_at=? WHERE id=?", (now(), run_id))
        final_core.add_event(run_id, 'analysis', 'web3.import.failed', '只读研究失败；检查导入产物与 Run 预算')


@router.post('/{import_id}/open')
def open_project(import_id: str):
    """Explicit local user action, restricted to this import's owned workspace."""
    import subprocess
    import sys
    value = read_job(import_id)
    if not value.get('source_path') or not value.get('finished_at'):
        raise HTTPException(409, '源码工程尚不可用')
    root = Path(value['source_path'])
    approved = data_directory() / 'web3-imports'
    if root.is_symlink() or not root.is_dir() or not root.resolve().is_relative_to(approved.resolve()):
        raise HTTPException(409, '工程目录不在批准的导入目录内')
    for parent in (root, *root.parents):
        if parent == approved.parent:
            break
        if parent.is_symlink():
            raise HTTPException(409, '工程目录不得包含符号链接')
    if sys.platform != 'darwin':
        raise HTTPException(409, '此入口需要 macOS Finder；CLI 可直接访问 source_path')
    try:
        subprocess.run(['/usr/bin/open', str(root)], check=True, timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(409, '无法打开工程，请在 Finder 中检查本地目录') from None
    return {'opened': True, 'import_id': import_id}
