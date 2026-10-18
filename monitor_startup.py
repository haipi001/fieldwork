"""Explicit user LaunchAgent configuration for the fixed monitor-only entry."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import uuid
import final_core as core
import agent_monitor_service as service
from version import APP_VERSION


def _location():
    root=core.LOCAL_DATA_ROOT
    label='com.fieldwork.monitor.'+hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return label,Path.home()/'Library/LaunchAgents'/(label+'.plist')


def _spec():
    from storage_migration import _plain_directory
    root=_plain_directory(core.LOCAL_DATA_ROOT)
    if core.DB.parent!=root:raise ValueError('Database outside data root')
    entry=core.ROOT/'monitor_login_worker.py'
    from proof_materials import read_proof_file
    read_proof_file(entry,256000)
    label,_=_location()
    return {'Label':label,'ProgramArguments':[sys.executable,'-E','-s',str(entry),'--database',str(core.DB),
        '--data-root',str(root),'--expected-version',APP_VERSION],
        'WorkingDirectory':str(core.ROOT),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},
        'ThrottleInterval':30,'ProcessType':'Background','StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'}


def _command(*args):
    try:return subprocess.run(['/bin/launchctl',*args],stdin=subprocess.DEVNULL,capture_output=True,
        timeout=10,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    except subprocess.TimeoutExpired:raise RuntimeError('LaunchAgent operation timed out') from None


def _registered(label):
    result=_command('print',f'gui/{os.getuid()}/{label}')
    if result.returncode==0:return True
    if result.returncode==113 and b'Could not find service' in result.stderr:return False
    raise RuntimeError('LaunchAgent registration cannot be verified')


def _record(path,desired):
    try:info=path.lstat()
    except FileNotFoundError:return None,None
    if info.st_uid!=os.getuid() or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('LaunchAgent file is not private and owner controlled')
    from proof_materials import read_proof_file
    raw=read_proof_file(path,32768)
    try:value=plistlib.loads(raw)
    except Exception:raise ValueError('Invalid LaunchAgent property list') from None
    # Old application versions may be removed, but arbitrary commands cannot.
    expected={**desired,'ProgramArguments':list(desired['ProgramArguments'])}
    if not isinstance(value,dict):raise ValueError('Invalid LaunchAgent')
    arguments=value.get('ProgramArguments')
    if not isinstance(arguments,list) or len(arguments)!=len(expected['ProgramArguments']):raise ValueError('Unexpected LaunchAgent command')
    if not isinstance(arguments[-1],str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',arguments[-1]):raise ValueError('Invalid LaunchAgent version')
    expected['ProgramArguments'][-1]=arguments[-1]
    if value!=expected:raise ValueError('Unrecognized LaunchAgent configuration')
    return raw,value


def plan():
    if sys.platform!='darwin':return {'supported':False,'configured':False,'registered':False,'state':'unsupported'}
    desired=_spec();label,path=_location();raw,value=_record(path,desired);registered=_registered(label)
    current=value==desired if value else False
    material={'desired':desired,'existing_sha256':hashlib.sha256(raw).hexdigest() if raw else None,'registered':registered}
    return {'supported':True,'configured':value is not None,'registered':registered,'matches_current':current,
        'state':'enabled' if current and registered else 'review_required' if value else 'unconfigured',
        'label':label,'app_version':APP_VERSION,'restart_on_failure':True,'restart_delay_seconds':30,
        'preview_hash':hashlib.sha256(json.dumps(material,sort_keys=True).encode()).hexdigest()}


def _write(path,raw):
    from storage_migration import _plain_directory,_open_directory
    _plain_directory(path.parent.parent)
    path.parent.mkdir(mode=0o700,exist_ok=True)
    parent=_open_directory(_plain_directory(path.parent));name='.'+uuid.uuid4().hex+'.tmp'
    try:
        descriptor=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        with os.fdopen(descriptor,'wb') as output:output.write(raw);output.flush();os.fsync(output.fileno())
        os.replace(name,path.name,src_dir_fd=parent,dst_dir_fd=parent)
    finally:
        try:os.unlink(name,dir_fd=parent)
        except FileNotFoundError:pass
        os.close(parent)


def apply(enabled,preview_hash):
    with service.process_lock('management.lock',blocking=False):
        current=plan()
        if not current.get('supported') or current.get('preview_hash')!=preview_hash:
            raise RuntimeError('Login startup plan changed; review again')
        label,path=_location()
        if enabled:
            if current['matches_current'] and current['registered']:return current
            if current['registered']:raise RuntimeError('Existing login job must be disabled before updating')
            import monitor_process_control as control
            actual=control.status()
            if actual.get('running') or actual.get('state') not in {'stopped'}:
                raise RuntimeError('Stop or recover the independent monitor before configuring login startup')
            service.ready()
            _write(path,plistlib.dumps(_spec(),sort_keys=True))
            result=_command('bootstrap',f'gui/{os.getuid()}',str(path))
            if result.returncode:raise RuntimeError('LaunchAgent bootstrap failed; configuration retained for review')
        else:
            if current['registered']:
                if not current['configured']:raise RuntimeError('LaunchAgent label is occupied by an unknown job')
                result=_command('bootout',f'gui/{os.getuid()}/{label}')
                if result.returncode:raise RuntimeError('LaunchAgent removal failed; configuration retained')
            try:
                with service.process_lock('service.lock',blocking=False):
                    import monitor_process_control as control
                    active=control._read('managed.json');owned=control._read('login-owned.json')
                    if active and owned and active['service_id']==owned['service_id']:
                        service.lock_path('managed.json').unlink(missing_ok=True)
            except BlockingIOError:pass
            if current['configured']:
                # All configuration mutations share management.lock and are hash reviewed.
                path.unlink()
        return plan()


def configured():
    _,path=_location()
    return path.exists() or path.is_symlink()


def start_registered():
    with service.process_lock('management.lock',blocking=False):
        current=plan()
        if not current.get('matches_current') or not current.get('registered'):raise RuntimeError('Review login startup configuration')
        import monitor_process_control as control
        actual=control.status()
        if actual.get('running'):raise RuntimeError('Monitor is already running')
        result=_command('kickstart',f'gui/{os.getuid()}/{current["label"]}')
        if result.returncode:raise RuntimeError('Login monitor start failed')
        return {'state':'starting','login_managed':True,'enabled_monitors_only':True}
