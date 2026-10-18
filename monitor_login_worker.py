"""Foreground launchd entry for enabled-monitor-only processing."""
import argparse
from pathlib import Path
import signal
import threading
import uuid
import final_core as core
import agent_monitor_service as service
import monitor_process_control as control
from version import APP_VERSION


def claim_login_instance():
    # Both locks prove an old login instance no longer owns execution. A pending
    # manual launch is never silently recovered by the login worker.
    with service.process_lock('management.lock'):
        with service.process_lock('service.lock',blocking=False):
            active=control._read('managed.json');previous=control._read('login-owned.json')
            if active and (not previous or previous['service_id']!=active['service_id']):
                raise RuntimeError('Unresolved manual monitor launch')
            service.ready()
            identifier=uuid.uuid4().hex
            control._write('login-owned.json',{'service_id':identifier})
            control._write('managed.json',{'service_id':identifier,'state':'starting'})
            return identifier


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True);parser.add_argument('--data-root',required=True)
    parser.add_argument('--expected-version',required=True);args=parser.parse_args()
    if args.expected_version!=APP_VERSION:parser.error('App version changed; review login configuration')
    from storage_migration import _plain_directory
    core.LOCAL_DATA_ROOT=_plain_directory(Path(args.data_root));core.DB=Path(args.database)
    if not core.DB.is_absolute() or core.DB.parent!=core.LOCAL_DATA_ROOT:parser.error('Database must belong to data root')
    stop=threading.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,lambda *_:stop.set())
    try:identifier=claim_login_instance()
    except BlockingIOError:return  # Another service actually holds execution.
    try:service.run(stop,service_id=identifier)
    finally:
        try:
            with service.process_lock('management.lock',blocking=False):
                active=control._read('managed.json')
                if active and active['service_id']==identifier:service.lock_path('managed.json').unlink(missing_ok=True)
        except BlockingIOError:
            pass  # bootout owner cleans the fenced login identity after exit.


if __name__=='__main__':main()
