"""Local monitor-only process. No HTTP listener, model calls or research scheduler.

Run after the App has initialized the database. The service processes only
monitors that the operator already enabled; it never creates a monitor.
"""
import argparse
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import threading
import time
import uuid

import final_core as core
from version import APP_VERSION, SCHEMA_VERSION


def lock_path(name):
    root = core.LOCAL_DATA_ROOT / 'monitor-service'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError('Monitor control directory must be a real directory')
    info=root.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise RuntimeError('Monitor control directory must be private to its owner')
    return root / name


@contextlib.contextmanager
def process_lock(name, blocking=True):
    path = lock_path(name)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(descriptor, 'a+') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise RuntimeError('Invalid monitor lock file')
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def ready():
    if not core.DB.is_file():
        raise RuntimeError('Open Fieldwork once to initialize its database')
    with sqlite3.connect(f'file:{core.DB}?mode=ro', uri=True) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise RuntimeError('Database version mismatch; stop service and upgrade using Fieldwork')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_monitor_policies'").fetchone():
            raise RuntimeError('Monitor schema is missing')


def heartbeat(error=None, service_id=None, *, failures=0, retry_after=None):
    target = lock_path('status.json')
    temporary = lock_path('heartbeat-'+uuid.uuid4().hex+'.tmp')
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump({'pid': os.getpid(), 'version': APP_VERSION, 'last_tick': time.time(), 'error': error, 'service_id':service_id,
                       'failures': failures, 'retry_after': retry_after}, handle)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def status():
    try:
        with process_lock('service.lock', blocking=False):
            return {'running': False, 'state': 'stopped'}
    except BlockingIOError:
        try:
            descriptor=os.open(lock_path('status.json'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(descriptor) as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise ValueError('Invalid heartbeat file')
                encoded=handle.read(4097)
            if len(encoded)>4096:raise ValueError('Oversized heartbeat')
            data=json.loads(encoded)
            if not isinstance(data,dict) or type(data.get('pid')) is not int or not 0<data['pid']<=2147483647:
                raise ValueError('Invalid heartbeat identity')
            if not isinstance(data.get('version'),str) or len(data['version'])>100:
                raise ValueError('Invalid heartbeat version')
            if type(data.get('last_tick')) not in (int,float) or not math.isfinite(data['last_tick']):
                raise ValueError('Invalid heartbeat time')
            identifier=data.get('service_id')
            if identifier is not None and (not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier)):
                raise ValueError('Invalid managed identity')
            error=data.get('error')
            if error is not None and (not isinstance(error,str) or len(error)>200):
                raise ValueError('Invalid heartbeat error')
            failures = data.get('failures', 0)
            retry_after = data.get('retry_after')
            if type(failures) is not int or not 0 <= failures <= 1_000_000:
                raise ValueError('Invalid failure count')
            if retry_after is not None and (type(retry_after) not in (int, float) or not math.isfinite(retry_after)
                    or not 0 <= retry_after <= data['last_tick'] + 60):
                raise ValueError('Invalid retry time')
            if (failures and (not error or retry_after is None)) or (retry_after is not None and (not error or not failures)):
                raise ValueError('Inconsistent retry state')
            age = time.time() - float(data['last_tick'])
            fresh = 0 <= age <= 15 and data['version'] == APP_VERSION
            state = 'backoff' if fresh and error and retry_after is not None and retry_after > time.time() else 'current' if fresh and not error else 'degraded'
            return {key:data.get(key) for key in ('pid','version','last_tick','error','service_id')} | {
                'running':True,'state':state,'failures':failures,'retry_after':retry_after}
        except (OSError, ValueError, KeyError, OverflowError, TypeError):
            return {'running': True, 'state': 'starting'}


def retry_delay(failures, interval=2):
    return min(60, interval * 2 ** min(max(failures - 1, 0), 5)) if failures else interval


def run(stop, step=None, interval=2, service_id=None):
    if not math.isfinite(interval) or not 0 <= interval <= 60:
        raise ValueError('Invalid monitor interval')
    if step is None:
        from agent_audit import scan_active_monitors
        step = scan_active_monitors
    from storage_access import storage_access
    with storage_access(core.LOCAL_DATA_ROOT.resolve()):
        with process_lock('service.lock', blocking=False):
            ready()
            failures = 0
            while not stop.is_set():
                if service_id:
                    from monitor_process_control import should_stop
                    if should_stop(service_id):break
                try:
                    ready()  # A live upgrade must never keep an old worker on a new schema.
                    step()
                    failures, error_name = 0, None
                except Exception as error:
                    failures = min(failures + 1, 1_000_000)
                    error_name = type(error).__name__
                delay = retry_delay(failures, interval)
                retry_after = time.time() + delay if error_name else None
                heartbeat(error_name, service_id=service_id, failures=failures, retry_after=retry_after)
                deadline = time.monotonic() + delay
                while not stop.is_set():
                    if service_id:
                        from monitor_process_control import should_stop
                        if should_stop(service_id):
                            return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or stop.wait(min(2, remaining)):
                        break
                    if error_name:
                        heartbeat(error_name, service_id=service_id, failures=failures, retry_after=retry_after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check schema readiness without collecting')
    parser.add_argument('--managed-id')
    parser.add_argument('--database')
    parser.add_argument('--data-root')
    args = parser.parse_args()
    if args.managed_id:
        if len(args.managed_id)!=32 or any(c not in '0123456789abcdef' for c in args.managed_id) or not args.database or not args.data_root:
            parser.error('Managed launch requires an identifier and storage paths')
        core.DB=Path(args.database).resolve()
        core.LOCAL_DATA_ROOT=Path(args.data_root).resolve()
        if core.DB.parent!=core.LOCAL_DATA_ROOT:parser.error('Database must belong to the data root')
    elif args.database or args.data_root:parser.error('Storage overrides require managed launch')
    if args.check:
        ready()
        print('Monitor service database ready')
        return
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        run(stop,service_id=args.managed_id)
    except BlockingIOError:
        raise SystemExit('A monitor service is already running')
    finally:
        if args.managed_id:
            from monitor_process_control import finished
            finished(args.managed_id)


if __name__ == '__main__':
    main()
