"""Actual macOS process isolation and fail-closed transport boundaries."""
import os
import json
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import v5_http_transport as transport
from traditional_runtime import ReplayRequest, VerificationCancelled
from tests.test_http_business_boundary import object_server


def test_live_pinned_get_is_independent_and_redacted(object_server):
    origin, calls = object_server
    value = transport.request_once(ReplayRequest(url=origin + '/me', headers={'X-Role': 'owner'}), ('127.0.0.1',))
    proof = value['process_execution']
    assert value['status'] == 200 and calls == [('/me', 'owner')]
    assert proof['child_pid'] != os.getpid() == proof['parent_pid']
    assert proof['file_read_denied'] and proof['exit_code'] == 0
    assert proof['network_grant'] == 'loopback_exact_port' and proof['scope'] == 'transport_only'
    assert len(proof['script_sha256']) == len(proof['sandbox_profile_sha256']) == 64
    assert 'headers' not in proof


@pytest.mark.parametrize('spec,addresses', [
    (ReplayRequest(url='http://127.0.0.1:1/', method='POST'), ('127.0.0.1',)),
    (ReplayRequest(url='http://127.0.0.1:1/', body='x'), ('127.0.0.1',)),
    (ReplayRequest(url='http://127.0.0.1:1/'), ()),
    (ReplayRequest(url='https://example.test/'), ('192.0.2.1',)),
])
def test_unsupported_requests_never_launch(monkeypatch, spec, addresses):
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises((ValueError, RuntimeError)):
        transport.request_once(spec, addresses)


def test_missing_sandbox_never_falls_back(monkeypatch, object_server):
    origin, calls = object_server
    monkeypatch.setattr(transport.sys, 'platform', 'linux')
    with pytest.raises(RuntimeError, match='requires the macOS sandbox'):
        transport.request_once(ReplayRequest(url=origin), ('127.0.0.1',))
    assert calls == []


def test_redirect_is_not_followed_and_host_override_is_rejected():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/second')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/first'
        assert transport.request_once(ReplayRequest(url=url), ('127.0.0.1',))['status'] == 302
        with pytest.raises(ValueError):
            transport.request_once(ReplayRequest(url=url, headers={'Host': 'another.test'}), ('127.0.0.1',))
        assert calls == ['/first']
    finally:
        server.shutdown()
        server.server_close()


def test_cancellation_reaps_worker_during_request(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(3)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    children = []
    real = transport.subprocess.Popen
    def launch(*args, **kwargs):
        child = real(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(transport.subprocess, 'Popen', launch)
    def check():
        if entered.is_set():
            raise VerificationCancelled('fixture cancellation')
    started = time.monotonic()
    try:
        with pytest.raises(VerificationCancelled):
            transport.request_once(ReplayRequest(url=f'http://127.0.0.1:{server.server_port}/'), ('127.0.0.1',), check)
        assert time.monotonic() - started < 2
        assert children and children[0].poll() is not None
    finally:
        release.set()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('oversize', [False, True])
def test_response_limits_and_redaction(oversize):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'x' * 65537 if oversize else json.dumps({'token': 'fixture-secret-value', 'id': 'A'}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        spec = ReplayRequest(url=f'http://127.0.0.1:{server.server_port}/')
        if oversize:
            with pytest.raises(ValueError):
                transport.request_once(spec, ('127.0.0.1',))
        else:
            result = transport.request_once(spec, ('127.0.0.1',))
            result.pop('_transient_body')
            assert 'fixture-secret-value' not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()


def test_https_does_not_disable_certificate_validation(tmp_path):
    key, cert = tmp_path / 'fixture-key.pem', tmp_path / 'fixture-cert.pem'
    subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(ValueError, match='SSLCertVerificationError'):
            transport.request_once(ReplayRequest(url=f'https://localhost:{server.server_port}/',
                                                headers={'Authorization': 'fixture-token'}), ('127.0.0.1',))
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
