"""Explicit permanent research reset; runtime configuration survives."""
from contextlib import ExitStack
import shutil
import json
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Literal
import final_core as f

router = APIRouter(prefix='/api/v1/maintenance', tags=['Research history'])
KEEP = {'runtime_profiles', 'runtime_providers', 'v5_schema_meta', 'v6_schema_meta',
        'runner_registry_v5', 'agent_registry_v5', 'agent_supply_components_v5'}
DIRECTORIES = ('artifacts', 'exports', 'agent_workspaces', 'repositories', 'agent_audit',
               'web3-imports', 'web3-practice', 'backups', 'restore-drills', 'evals')


class PurgeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: Literal['DELETE_ALL_RESEARCH']


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def blockers(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    checks = {
        'analysis_runs': "status IN ('queued','running','paused')",
        'runs': "status IN ('queued','running','paused')",
        'agent_tasks': "status IN ('leased','running','paused') OR lease_owner IS NOT NULL",
        'guided_research_jobs': "status IN ('queued','running')",
        'verification_jobs': "status IN ('queued','running','cancelling')",
        'runtime_calls': "state IN ('reserved','calling','unknown')",
        'agent_monitors': "status='active'",
        'campaign_schedules': 'enabled=1',
    }
    return [table for table, predicate in checks.items() if table in tables and
            db.execute('SELECT 1 FROM '+quote(table)+' WHERE '+predicate+' LIMIT 1').fetchone()]


def inventory(db):
    return {r[0]: db.execute('SELECT COUNT(*) FROM '+quote(r[0])).fetchone()[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            if r[0] not in KEEP}


@router.get('/all-research')
def preview():
    import v5_research_worker as worker
    with worker._lock:
        executor_running=any(job.is_alive() for job in worker._jobs.values())
    with f.connect() as db:
        pending=db.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='queued' AND lease_owner IS NULL").fetchone()[0]
        return {'counts': inventory(db), 'pending_tasks':pending, 'blockers': blockers(db)+(['research_executor'] if executor_running else []), 'permanent': True,
                'includes_local_backups': True, 'keeps_model_configuration': True}


@router.delete('/all-research')
def purge(body: PurgeInput):
    # The API mutation gate prevents concurrent starts. Monitor process locks
    # additionally prevent its independent executable from starting mid-reset.
    import agent_monitor_service as service
    import monitor_process_control as control
    import web3_import_api as imports
    import v5_research_worker as worker
    root = f.LOCAL_DATA_ROOT.resolve()
    moved = []
    trash = root / ('.research-purge-' + uuid.uuid4().hex)
    try:
        with service.process_lock('management.lock', blocking=False), imports.LOCK, worker._lock, ExitStack() as locks:
            if any(job.is_alive() for job in worker._jobs.values()):
                raise HTTPException(409, '研究执行器仍在运行，请先停止后重试')
            state = control.status()
            if state['running'] or state['state'] in ('starting', 'unresolved'):
                raise HTTPException(409, '请先停止独立监控进程')
            locks.enter_context(service.process_lock('service.lock', blocking=False))
            if any(not task.done() for task in imports.TASKS.values()):
                raise HTTPException(409, '请先等待合约导入任务结束')
            with f.connect() as db:
                db.execute('PRAGMA foreign_keys=OFF')
                db.execute('PRAGMA secure_delete=ON')
                db.execute('BEGIN IMMEDIATE')
                active = blockers(db)
                if active:
                    raise HTTPException(409, {'message': '请先停止活动研究、监控和调度，并结清模型用量', 'blockers': active})
                counts = inventory(db)
                triggers = db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
                # Never follow a registered artifact out of the managed data root.
                for row in db.execute('SELECT uri FROM artifacts'):
                    uri = row[0]
                    if uri and Path(uri).is_absolute() and not Path(uri).resolve().is_relative_to(root):
                        raise HTTPException(409, '存在数据目录外的证据，无法保证完整清除')
                owned_sessions=[row[0] for row in db.execute(
                    "SELECT id FROM identities WHERE credential_ref='keychain://fieldwork-session/' || id")]
                trash.mkdir(mode=0o700)
                # Persist cleanup obligations before committing so a retry can
                # remove owned session credentials even after rows are gone.
                (trash/'owned-sessions.json').write_text(json.dumps(owned_sessions))
                (trash/'owned-sessions.json').chmod(0o600)
                for name in DIRECTORIES:
                    path = root / name
                    if path.is_symlink():
                        raise HTTPException(409, '记录目录不能是符号链接')
                    if path.exists():
                        path.rename(trash / name)
                        moved.append(name)
                for trigger in triggers:
                    if trigger['tbl_name'] in counts:
                        db.execute('DROP TRIGGER '+quote(trigger['name']))
                for table in counts:
                    db.execute('DELETE FROM '+quote(table))
                for trigger in triggers:
                    if trigger['tbl_name'] in counts:
                        db.execute(trigger['sql'])
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise HTTPException(409, '清除后的数据库关联检查失败')
                db.commit()
    except Exception as error:
        for name in reversed(moved):
            (trash / name).rename(root / name)
        if trash.exists():
            (trash/'owned-sessions.json').unlink(missing_ok=True)
            trash.rmdir()
        if isinstance(error, BlockingIOError):
            raise HTTPException(409, '独立监控仍在运行或控制状态正在变化，请先停止后重试') from None
        raise
    try:
        remove_staged_history(trash)
        # Prior incomplete filesystem removal is retried, never called success.
        for remaining in root.glob('.research-purge-*'):
            if remaining.is_dir() and not remaining.is_symlink():
                remove_staged_history(remaining)
    except (OSError,RuntimeError,ValueError):
        raise HTTPException(500, '数据库记录已清空，但文件或测试会话凭据清理未完成；请再次清除，暂不可视为全部删除') from None
    return {'status': 'deleted', 'removed_records': sum(counts.values()), 'permanent': True,
            'includes_local_backups': True, 'keeps_model_configuration': True}


def remove_staged_history(directory):
    import session_capture
    marker=directory/'owned-sessions.json'
    if marker.exists():
        if marker.is_symlink() or marker.stat().st_size>1024*1024:
            raise ValueError('invalid_purge_cleanup_marker')
        sessions=json.loads(marker.read_text())
        if not isinstance(sessions,list) or any(not isinstance(identity,str) or not 0<len(identity)<=256 for identity in sessions):
            raise ValueError('invalid_purge_cleanup_marker')
        for identity in sessions:
            session_capture.delete_keychain(identity)
    shutil.rmtree(directory)
