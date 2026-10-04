"""Actual sandbox workers stop when their owned supervisor is SIGKILLed."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


SUPERVISOR = '''
import json, selectors, subprocess, sys
from pathlib import Path
import v5_http_transport as transport
import v5_verification as verification
from traditional_runtime import ReplayRequest
config = json.loads(sys.stdin.readline())
real = subprocess.Popen
def observe(*args, **kwargs):
    child = real(*args, **kwargs)
    if config['kind'] == 'verifier':
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(5) or child.stdout.readline() != b'READY\\n':
                child.kill()
                child.wait()
                raise RuntimeError('worker did not start')
    print(json.dumps({'pid': child.pid}), flush=True)
    return child
subprocess.Popen = observe
if config['kind'] == 'verifier':
    verification.CHILD_SCRIPT = Path(config['script'])
    verification._run_local_verifier({'type': 'package_applicability_v1'})
else:
    transport.request_once(ReplayRequest(url=config['url']), ('127.0.0.1',))
'''


def running(pid):
    value = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                           capture_output=True, text=True, timeout=2).stdout.strip()
    return bool(value) and not value.startswith('Z')


@pytest.mark.parametrize('kind', ['http', 'verifier'])
def test_active_sandbox_child_exits_after_supervisor_sigkill(tmp_path, kind):
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(10)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'fixture')
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    script = tmp_path / 'waiting-verifier.py'
    script.write_text('import time\nprint("READY", flush=True)\ntime.sleep(60)\n')
    config = {'kind': kind, 'script': str(script), 'url': f'http://127.0.0.1:{server.server_port}/'}
    process = subprocess.Popen([sys.executable, '-c', SUPERVISOR],
                               cwd=Path(__file__).resolve().parents[1],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    child_pid = None
    try:
        process.stdin.write(json.dumps(config) + '\n')
        process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), 'Supervisor did not launch worker'
        line = process.stdout.readline()
        assert line, process.stderr.read()
        child_pid = json.loads(line)['pid']
        if kind == 'http':
            assert entered.wait(5), 'Real HTTP worker did not enter response wait'
        assert running(child_pid)
        started = time.monotonic()
        process.kill()
        assert process.wait(5) == -signal.SIGKILL
        while running(child_pid) and time.monotonic() - started < 3:
            time.sleep(.02)
        assert not running(child_pid), 'Worker survived supervisor death'
        assert time.monotonic() - started < 3
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(5)
        if child_pid and running(child_pid):
            os.kill(child_pid, signal.SIGKILL)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(5)
