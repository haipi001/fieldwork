"""Manage the fixed monitor-only executable; never create or enable a monitor."""
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import uuid
import final_core as core
import agent_monitor_service as service

_children = {}


def _read(name):
    path=service.lock_path(name)
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid monitor record file')
        encoded=stream.read(4097)
        if len(encoded)>4096:raise ValueError('Oversized monitor record')
        value=json.loads(encoded)
    if not isinstance(value,dict):raise ValueError('Invalid monitor control record')
    identifier=value.get('service_id')
    if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid monitor control identity')
    if set(value)-{'service_id','state'} or ('state' in value and value['state']!='starting'):
        raise ValueError('Invalid monitor control fields')
    return value


def _write(name,value):
    target=service.lock_path(name)
    temporary=service.lock_path(uuid.uuid4().hex+'.tmp')
    descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,'w') as stream:json.dump(value,stream)
        os.replace(temporary,target)
    finally:
        temporary.unlink(missing_ok=True)


def status():
    actual=service.status()
    pending=_read('managed.json')
    for identifier,child in list(_children.items()):
        if child.poll() is not None and (not pending or pending.get('service_id')!=identifier):
            _children.pop(identifier,None)
    if pending:
        child=_children.get(pending['service_id'])
        if child and child.poll() is not None and not actual['running']:
            return {**actual,'managed':False,'launch_failed':True,'exit_code':child.returncode,'service_id':pending['service_id']}
        if not actual['running']:
            return {**pending,'running':False,'state':'starting' if child else 'unresolved','managed':True,
                    'pid':child.pid if child else None,
                    'stop_requested':(_read('stop.json') or {}).get('service_id')==pending['service_id']}
    return {**actual,'managed':bool(actual.get('service_id')),'stop_requested':bool(actual.get('service_id') and (_read('stop.json') or {}).get('service_id')==actual.get('service_id'))}


def start():
    import monitor_startup
    if monitor_startup.configured():return monitor_startup.start_registered()
    with service.process_lock('management.lock',blocking=False):
        current=status()
        if current.get('launch_failed'):
            service.lock_path('managed.json').unlink(missing_ok=True)
        elif current['running'] or current['state'] in ('starting','unresolved'):
            raise RuntimeError('Monitor process already running or launch unresolved')
        service.ready()
        service_id=uuid.uuid4().hex
        _write('managed.json',{'service_id':service_id,'state':'starting'})
        try:
            child=subprocess.Popen([sys.executable,'-m','agent_monitor_service','--managed-id',service_id,
                                    '--database',str(core.DB.resolve()),'--data-root',str(core.LOCAL_DATA_ROOT.resolve())],
                                   cwd=str(core.ROOT),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True,
                                   env={**{key:value for key,value in os.environ.items() if key not in {
                                       'FIELDWORK_SESSION_TOKEN','FIELDWORK_DESKTOP_INSTANCE','FIELDWORK_PORT','FIELDWORK_DESKTOP_PARENT_PID'}},
                                        'PYTHONPATH':str(Path(__file__).resolve().parent)})
            _children[service_id]=child
            return {'service_id':service_id,'state':'starting','pid':child.pid,'enabled_monitors_only':True}
        except Exception:
            service.lock_path('managed.json').unlink(missing_ok=True)
            raise


def stop(service_id):
    with service.process_lock('management.lock',blocking=False):
        current=status()
        if current.get('service_id')!=service_id or not current.get('managed'):
            raise RuntimeError('Managed process changed or cannot be identified')
        _write('stop.json',{'service_id':service_id})
        return {'service_id':service_id,'stop_requested':True,'state':'stopping'}


def should_stop(service_id):
    active=_read('managed.json')
    return (not active or active.get('service_id')!=service_id or
            (_read('stop.json') or {}).get('service_id')==service_id)


def recover(service_id):
    """Invalidate an orphan launch while holding both control and execution locks.

    A delayed child must acquire service.lock and check its identity before any
    collection, so deleting this identity fences it without signalling a PID.
    """
    with service.process_lock('management.lock',blocking=False):
        current=status()
        if current.get('state')!='unresolved' or current.get('service_id')!=service_id:
            raise RuntimeError('Launch identity changed or recovery is not available')
        with service.process_lock('service.lock',blocking=False):
            active=_read('managed.json')
            if not active or active.get('service_id')!=service_id:
                raise RuntimeError('Launch identity changed')
            service.lock_path('managed.json').unlink()
            return {'service_id':service_id,'launch_invalidated':True,'state':'stopped'}


def finished(service_id):
    with service.process_lock('management.lock'):
        active=_read('managed.json')
        if active and active.get('service_id')==service_id:
            service.lock_path('managed.json').unlink(missing_ok=True)
