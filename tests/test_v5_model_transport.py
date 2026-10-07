import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest
import v5_model_transport as transport


def test_http_error_reports_only_status_without_response_body():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'fixture-private-credential-and-provider-error-body')
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(transport.ModelTransportError) as failure:
            transport.request_model(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', {}, 2000)
        assert failure.value.code == 'http_status_401'
        assert 'fixture-private' not in str(failure.value)
        assert 'provider-error-body' not in str(failure.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('stop', ['deadline', 'cancel'])
def test_slow_continuous_response_is_terminated_and_child_reaped(stop, monkeypatch):
    requested = threading.Event()
    disconnected = threading.Event()
    children = []
    original = transport.spawn_child
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(transport, 'spawn_child', spawn)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '10000')
            self.end_headers()
            requested.set()
            try:
                for _ in range(200):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def check():
        if stop == 'cancel' and requested.is_set():
            raise RuntimeError('task canceled')
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError if stop == 'deadline' else RuntimeError,
                           match='wall-clock|task canceled'):
            transport.request_model(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
                                    {'model': 'fixture', 'messages': []},
                                    1500 if stop == 'deadline' else 3000, check)
        assert requested.is_set()
        assert time.monotonic() - start < 3
        assert len(children) == 1 and children[0].poll() is not None
        assert children[0]._fieldwork_lifetime_writer is None
        assert disconnected.wait(1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_supervisor_death_closes_transport_child():
    import os
    from pathlib import Path
    import select
    import subprocess
    import sys
    requested = threading.Event()
    disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '10000')
            self.end_headers()
            requested.set()
            try:
                for _ in range(200):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    code = '''import sys
import v5_model_transport as t
original = t.spawn_child
def spawn(*a, **kw):
    process = original(*a, **kw)
    print(process.pid, flush=True)
    return process
t.spawn_child = spawn
t.request_model(sys.argv[1], {"model":"fixture","messages":[]}, 45000)
'''
    parent = subprocess.Popen([sys.executable, '-c', code,
                               f'http://127.0.0.1:{server.server_port}/v1/chat/completions'],
                              cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
    child_pid = None
    try:
        assert select.select([parent.stdout], [], [], 3)[0]
        child_pid = int(parent.stdout.readline())
        assert requested.wait(3)
        parent.kill()
        parent.wait(timeout=2)
        assert disconnected.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = subprocess.run(['ps', '-o', 'stat=', '-p', str(child_pid)],
                                    capture_output=True, text=True).stdout.strip()
            if not status or status.startswith('Z'):
                break
            time.sleep(.05)
        assert not status or status.startswith('Z'), status
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=2)
        if child_pid:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
