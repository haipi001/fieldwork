"""Owned backend exits when its desktop lifetime writer disappears."""
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import socket
import subprocess
import sys
import time

import pytest

from tests.test_asgi_cold_start import SERVER, request
from tests.test_final import client
from tests.test_parent_bound_child import running


BACKEND = SERVER.rsplit('import uvicorn\n', 1)[0] + '''
from desktop_server import run_owned_server
run_owned_server(app.app, config['port'], fd=config['fd'])
'''

OWNER = '''
import json, os, subprocess, sys, time
config = json.loads(sys.stdin.readline())
environment = dict(os.environ, FIELDWORK_DESKTOP_PARENT_PID=str(os.getpid()))
process = subprocess.Popen([sys.executable, '-c', config.pop('backend')],
                           stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           pass_fds=(config['fd'],), env=environment, text=True)
process.stdin.write(json.dumps(config) + '\\n')
process.stdin.flush()
print(json.dumps({'pid': process.pid}), flush=True)
if config['close_only']:
    sys.stdin.readline()
    process.stdin.close()
    process.wait(10)
    print('STOPPED', flush=True)
else:
    time.sleep(60)
'''


@pytest.mark.parametrize('close_only', [True, False], ids=['writer-close', 'owner-sigkill'])
def test_real_owned_backend_stops_with_lifetime_pipe(client, tmp_path, close_only):
    from final_core import DB
    token, instance = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(32)
        port = listener.getsockname()[1]
        config = {'db': str(DB), 'data': str(tmp_path), 'fd': listener.fileno(), 'port': port,
                  'token': token, 'instance': instance, 'backend': BACKEND, 'close_only': close_only}
        owner = subprocess.Popen([sys.executable, '-c', OWNER],
                                 cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 pass_fds=(listener.fileno(),))
        backend_pid = None
        try:
            owner.stdin.write(json.dumps(config) + '\n')
            owner.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(owner.stdout, selectors.EVENT_READ)
                assert selector.select(10)
            line = owner.stdout.readline()
            assert line, owner.stderr.read()
            backend_pid = json.loads(line)['pid']
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 10
            while True:
                assert running(backend_pid)
                try:
                    status, headers, _ = request(base, '/health')
                    if status == 200:
                        break
                except OSError:
                    pass
                assert time.monotonic() < deadline
                time.sleep(.05)
            assert headers['X-Fieldwork-Instance'] == instance
            assert request(base, '/v5', token)[0] == 200
            if close_only:
                owner.stdin.write('close\n')
                owner.stdin.flush()
            else:
                owner.kill()
                assert owner.wait(5) == -signal.SIGKILL
            deadline = time.monotonic() + 7
            while running(backend_pid) and time.monotonic() < deadline:
                time.sleep(.05)
            assert not running(backend_pid), 'Backend survived its desktop lifetime writer'
            if close_only:
                assert owner.wait(5) == 0
                assert owner.stdout.readline().strip() == 'STOPPED'
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.wait(5)
            if backend_pid and running(backend_pid):
                os.kill(backend_pid, signal.SIGKILL)
            for stream in (owner.stdin, owner.stdout, owner.stderr):
                stream.close()


def test_desktop_backend_rejects_missing_parent_binding():
    environment = dict(os.environ)
    environment.pop('FIELDWORK_DESKTOP_PARENT_PID', None)
    result = subprocess.run([sys.executable, '-m', 'desktop_server', '--port', '8000'],
                            input='', capture_output=True, text=True, env=environment, timeout=5)
    assert result.returncode != 0
    assert 'requires its parent lifetime pipe' in result.stderr


def test_safe_path_owned_launcher_uses_explicit_source_and_storage(tmp_path):
    """Exercise the native launch arguments from /, with no cwd import search."""
    root=Path(__file__).resolve().parents[1]
    directory=tmp_path.resolve()/'data';directory.mkdir(mode=0o700)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    token,instance=secrets.token_urlsafe(48),secrets.token_urlsafe(24)
    environment=dict(os.environ,PYTHONPATH=str(root),FIELDWORK_DATA_DIRECTORY=str(directory),
                     FIELDWORK_DESKTOP_PARENT_PID=str(os.getpid()),FIELDWORK_PORT=str(port),
                     FIELDWORK_SESSION_TOKEN=token,FIELDWORK_DESKTOP_INSTANCE=instance,
                     FIELDWORK_DISABLE_AGENT_MONITOR='1',FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER='1')
    environment.pop('PYTEST_CURRENT_TEST',None)
    environment.pop('SRC_ENABLE_SYNTHETIC_DEMO',None)
    process=subprocess.Popen([sys.executable,'-P','-m','desktop_server','--port',str(port)],cwd='/',
                             env=environment,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        base=f'http://127.0.0.1:{port}';deadline=time.monotonic()+15
        while True:
            assert process.poll() is None,'Owned backend exited before ready'
            try:
                status,headers,_=request(base,'/health')
                if status==200:break
            except OSError:pass
            assert time.monotonic()<deadline,'Owned backend startup timed out'
            time.sleep(.05)
        assert headers['X-Fieldwork-Instance']==instance
        assert headers['X-Fieldwork-Version']==__import__('version').APP_VERSION
        assert request(base,'/api/v1/workspace/maintenance')[0]==401
        status,_,payload=request(base,'/api/v1/workspace/maintenance',token)
        assert status==200 and json.loads(payload)['data_directory']==str(directory)
        assert request(base,'/health',extra={'Host':'external.example.test'})[0]==403
        process.stdin.close()
        assert process.wait(timeout=10)==0
    finally:
        if process.poll() is None:
            process.stdin.close() if not process.stdin.closed else None
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
