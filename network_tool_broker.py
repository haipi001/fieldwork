"""Read-only scanner proxy: a sandboxed tool reaches only this local broker.

Each GET is independently scoped, DNS-pinned, rate-limited and charged to its Run.
HTTPS is terminated locally; upstream TLS is independently verified by the transport worker.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import base64
import secrets
import threading
import time
from urllib.parse import urlsplit


@contextmanager
def scanner_broker(run_id, timeout):
    import final_core as f
    import traditional_runtime as http
    from v5_http_transport import request_once
    from network_broker_tls import broker_authority
    initial = f.get_run(run_id)
    initial_engagement = f.get_engagement(initial['engagement_id'])
    initial_authority = (f.dump(initial_engagement['scope']), f.dump(initial_engagement['policy']))
    deadline = time.monotonic() + timeout
    lock = threading.Lock()
    token = secrets.token_urlsafe(32)
    authorization = 'Basic ' + base64.b64encode(('fieldwork:' + token).encode()).decode()
    state = {'requests': 0, 'responses': 0, 'denied': 0, 'broker_probes': 0, 'last_request': 0.0, 'errors': [], '_token': token}
    state['tls_sessions'] = 0

    def current():
        if time.monotonic() >= deadline:
            raise ValueError('scanner_deadline')
        run = f.get_run(run_id)
        engagement = f.get_engagement(run['engagement_id'])
        if run['status'] in {'paused', 'stopped', 'failed', 'timeout', 'budget_exhausted'}:
            raise ValueError('run_not_executable')
        if (run['scope_snapshot_id'] != initial['scope_snapshot_id'] or run['policy_id'] != initial['policy_id']
                or engagement['current_scope_snapshot_id'] != run['scope_snapshot_id']
                or engagement['current_policy_id'] != run['policy_id']):
            raise ValueError('authority_changed')
        if (f.dump(engagement['scope']), f.dump(engagement['policy'])) != initial_authority:
            raise ValueError('authority_changed')
        return engagement

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            self.request.settimeout(8)
            super().setup()
        def authenticated(self):
            if getattr(self, 'tunnel_origin', None):
                return True
            if secrets.compare_digest(self.headers.get('Proxy-Authorization', ''), authorization):
                return True
            self.send_response(407)
            self.send_header('Proxy-Authenticate', 'Basic realm="Fieldwork"')
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            return False
        def reject(self, reason, status=403):
            with lock:
                state['denied'] += 1
                if reason not in state['errors']:
                    state['errors'].append(reason)
            self.send_response(status)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
        def do_CONNECT(self):
            if not self.authenticated():
                return
            try:
                endpoint = urlsplit('https://' + self.path)
                if not endpoint.hostname or endpoint.username or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment:
                    raise ValueError('invalid_connect')
                engagement = current()
                origins = [urlsplit(target) for target in engagement['scope'].get('allowed_targets', [])]
                if not any(item.scheme == 'https' and item.hostname == endpoint.hostname
                           and (item.port or 443) == (endpoint.port or 443) for item in origins):
                    self.reject('scanner_request_out_of_scope')
                    return
                context = tls_context(endpoint.hostname)
                self.send_response(200, 'Connection Established')
                self.end_headers()
                self.wfile.flush()
                self.close_connection = True
                with context.wrap_socket(self.connection, server_side=True) as secured:
                    with lock:
                        state['tls_sessions'] += 1
                    class TunnelHandler(Handler):
                        tunnel_origin = 'https://' + self.path
                    TunnelHandler(secured, self.client_address, self.server)
            except Exception:
                self.close_connection = True
        def do_GET(self):
            if not self.authenticated():
                return
            try:
                target = self.path
                if getattr(self, 'tunnel_origin', None):
                    if not target.startswith('/') or target.startswith('//'):
                        raise ValueError('unsupported_scanner_request')
                    target = self.tunnel_origin + target
                parsed = urlsplit(target)
                if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                        or parsed.fragment or self.headers.get('Transfer-Encoding')
                        or self.headers.get('Content-Length', '0') != '0'):
                    raise ValueError('unsupported_scanner_request')
                with lock:
                    engagement = current()
                    # Katana detects Burp proxies with this reserved probe.
                    # Answer locally, never resolve it or count it as target evidence.
                    if self.path == 'http://burpsuite/' and not any(
                            urlsplit(target).hostname == 'burpsuite' for target in engagement['scope'].get('allowed_targets', [])):
                        state['broker_probes'] += 1
                        self.send_response(404)
                        self.send_header('Content-Length', '0')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        return
                    decision = f.execution_policy_check(f.PolicyCheckInput(
                        engagement_id=engagement['id'], target=target, action='read'))
                    if not decision['allowed']:
                        raise ValueError('scanner_request_out_of_scope')
                    forbidden_headers = {'host', 'connection', 'content-length', 'transfer-encoding', 'proxy-authorization',
                        'proxy-connection', 'upgrade', 'te', 'trailer', 'keep-alive', 'accept-encoding'}
                    headers = {name: value for name, value in self.headers.items() if name.lower() not in forbidden_headers}
                    if any(name.lower() in {'authorization', 'cookie', 'x-api-key'} for name in headers):
                        if not engagement['scope'].get('allow_authentication') or parsed.hostname not in engagement['scope'].get('auth_allowed_hosts', []):
                            raise ValueError('scanner_authentication_not_authorized')
                    spec = http.ReplayRequest(url=target, headers=headers)
                    addresses = http.network_guard(engagement, spec)
                    rate = max(0.1, float(engagement['policy'].get('max_requests_per_second', 1)))
                    remaining = 1 / rate - (time.monotonic() - state['last_request'])
                    while remaining > 0:
                        time.sleep(min(remaining, .1))
                        current()
                        remaining = 1 / rate - (time.monotonic() - state['last_request'])
                    allowed, reason = f.consume_run_budget(run_id, 'request', 1)
                    if not allowed:
                        raise ValueError('scanner_request_budget')
                    state['requests'] += 1
                    state['last_request'] = time.monotonic()
                from http_tls_trust import issuer_for
                issuer = issuer_for(engagement['scope'], target)
                options = {'trusted_ca_pem': issuer} if issuer is not None else {}
                options['raw_body'] = True
                options['raw_headers'] = True
                response = request_once(spec, addresses, current, **options)
                response.pop('_transient_body')
                body = response.pop('_transient_bytes')
                if hashlib.sha256(body).hexdigest() != response['body_sha256']:
                    raise ValueError('scanner_response_integrity')
                with lock:
                    state['responses'] += 1
                self.send_response(response['status'])
                for name, value in response.pop('_transient_headers'):
                    if name.lower() == 'set-cookie':
                        parts = value.split(';', 1)
                        value = parts[0].split('=', 1)[0] + '=[REDACTED]' + (';' + parts[1] if len(parts) > 1 else '')
                    if name.lower() not in {'content-length', 'connection', 'transfer-encoding', 'proxy-authenticate', 'upgrade', 'trailer'} and '\r' not in value and '\n' not in value:
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
            except Exception as error:
                allowed_reasons = {'scanner_deadline', 'run_not_executable', 'authority_changed',
                    'unsupported_scanner_request', 'scanner_request_out_of_scope', 'scanner_request_budget',
                    'scanner_response_integrity', 'scanner_authentication_not_authorized'}
                reason = str(error) if isinstance(error, ValueError) and str(error) in allowed_reasons else 'scanner_request_rejected'
                self.reject(reason)
        def do_POST(self):
            if not self.authenticated():
                return
            self.reject('scanner_method_not_authorized')
        do_HEAD = do_POST
        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
        do_OPTIONS = do_POST
        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        slots = threading.BoundedSemaphore(4)
        def process_request(self, connection, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(connection)
                return
            try:
                super().process_request(connection, address)
            except BaseException:
                self.slots.release()
                raise
        def process_request_thread(self, connection, address):
            try:
                super().process_request_thread(connection, address)
            finally:
                self.slots.release()

    server = Server(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.timeout = .2
    with broker_authority() as (ca_pem, tls_context):
        state['_ca_pem'] = ca_pem
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port, state
        finally:
            deadline = 0
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
