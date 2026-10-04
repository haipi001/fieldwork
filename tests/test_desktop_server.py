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
