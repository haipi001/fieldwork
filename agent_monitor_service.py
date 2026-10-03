"""Local monitor-only process. No HTTP listener, model calls or research scheduler.

Run after the App has initialized the database. The service processes only
monitors that the operator already enabled; it never creates a monitor.
"""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

import final_core as core
from version import APP_VERSION, SCHEMA_VERSION


def lock_path(name):
    root = core.LOCAL_DATA_ROOT / 'monitor-service'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / name


@contextlib.contextmanager
def process_lock(name, blocking=True):
    path = lock_path(name)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a+') as handle:
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


def heartbeat(error=None):
    target = lock_path('status.json')
    temporary = target.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({'pid': os.getpid(), 'version': APP_VERSION, 'last_tick': time.time(), 'error': error}, handle)
    os.replace(temporary, target)


def status():
    try:
        with process_lock('service.lock', blocking=False):
            return {'running': False, 'state': 'stopped'}
    except BlockingIOError:
        try:
            data = json.loads(lock_path('status.json').read_text())
            age = time.time() - float(data['last_tick'])
            return {**data, 'running': True, 'state': 'current' if 0 <= age <= 15 and not data.get('error') else 'degraded'}
        except (OSError, ValueError, KeyError):
            return {'running': True, 'state': 'starting'}


def run(stop, step=None, interval=2):
    if step is None:
        from agent_audit import scan_active_monitors
        step = scan_active_monitors
    with process_lock('service.lock', blocking=False):
        ready()
        while not stop.is_set():
            try:
                ready()  # A live upgrade must never keep an old worker on a new schema.
                step()
                heartbeat()
            except Exception as error:
                heartbeat(type(error).__name__)
            stop.wait(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check schema readiness without collecting')
    args = parser.parse_args()
    if args.check:
        ready()
        print('Monitor service database ready')
        return
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    try:
        run(stop)
    except BlockingIOError:
        raise SystemExit('A monitor service is already running')


if __name__ == '__main__':
    main()
