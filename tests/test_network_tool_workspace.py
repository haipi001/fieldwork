"""URL tools must never snapshot application data or unrelated source trees."""
from pathlib import Path
import pytest
import capability_registry
import final_core
import traditional_runtime
import traditional_tools

@pytest.mark.parametrize('capability', ['httpx', 'katana', 'nuclei', 'subfinder'])
@pytest.mark.parametrize('raises', [False, True])
def test_network_tools_use_empty_private_workspace_and_clean_it(monkeypatch, tmp_path, capability, raises):
    # The mocked Run still reads its frozen Nuclei configuration. Keep that
    # lookup isolated from the developer's default research database.
    monkeypatch.setattr(final_core, 'DB', tmp_path / 'workspace-test.db')
    with final_core.connect() as db:
        db.execute('CREATE TABLE run_configs_v2 (run_id TEXT PRIMARY KEY, config TEXT)')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'private-data.txt').write_text('fixture-private-material')
    (source / 'python').symlink_to('/usr/bin/python3')
    engagement = {'id': 'project', 'normalized_target': 'https://example.test', 'policy': {}}
    monkeypatch.setattr(traditional_tools, '_load_run', lambda _: ({'id': 'run'}, engagement))
    monkeypatch.setattr(final_core, 'execution_policy_check', lambda _: {'allowed': True})
    monkeypatch.setattr(traditional_runtime, 'network_guard', lambda *args: None)
    monkeypatch.setattr(final_core, 'consume_run_budget', lambda *args: (True, 'allowed'))
    monkeypatch.setattr(traditional_tools, '_persist_result', lambda *args, **kwargs: {'status': args[2].status})
    from contextlib import contextmanager
    import network_tool_broker
    @contextmanager
    def broker(*args):
        yield 5555, {'requests': 1, 'responses': 1, 'denied': 0, 'errors': [], '_token': 'isolated-test-token'}
    monkeypatch.setattr(network_tool_broker, 'scanner_broker', broker)
    workspaces = []
    def execute(identifier, args, cwd, timeout, **options):
        workspaces.append(cwd)
        assert cwd != source and cwd != Path(traditional_tools.__file__).parent
        assert all(path.name == 'model.json' or path.name.startswith('template-') for path in cwd.iterdir())
        if raises:
            raise OSError('fixture tool launch failure')
        return capability_registry.ToolResultEnvelope(identifier, 'completed', 0, '', '')
    monkeypatch.setattr(capability_registry, 'execute', execute)
    if raises:
        with pytest.raises(Exception, match='source_snapshot_failed'):
            traditional_tools.run_capability('run', capability, str(source), 10)
    else:
        assert traditional_tools.run_capability('run', capability, str(source), 10)['status'] == 'completed'
    assert len(workspaces) == 1 and not workspaces[0].exists()
    assert (source / 'private-data.txt').read_text() == 'fixture-private-material'
    assert (source / 'python').is_symlink()


@pytest.mark.parametrize('capability', ['httpx', 'katana', 'nuclei'])
@pytest.mark.parametrize('protocol', ['http', 'https'])
def test_actual_network_scanner_runs_in_isolation_against_authorized_loopback(client, capability, protocol):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get('Proxy-Authorization') is None
            requests.append(self.path)
            body = b'<html><title>Fieldwork isolated acceptance</title></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    from network_broker_tls import broker_authority
    authority = broker_authority()
    ca, context = authority.__enter__()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if protocol == 'https':
        server.socket = context('127.0.0.1').wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f'{protocol}://127.0.0.1:{server.server_port}'
        created = client.post('/api/v1/engagements', json={'name': 'Local isolated tool acceptance', 'target': origin,
            'mode': 'traditional', 'scope': {'allow_private_ips': True, 'http_tls_trust': {origin: ca.decode()} if protocol == 'https' else {}}, 'policy': {}}).json()
        engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
        run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()
        with final_core.connect() as db:
            db.execute('UPDATE run_budgets_v2 SET request_limit=100,tool_call_limit=10 WHERE run_id=?', (run['id'],))
        result = client.post(f"/api/v1/traditional/runs/{run['id']}/tools/run", json={'capability': capability, 'timeout_seconds': 20})
        assert result.status_code == 200, result.text
        value = result.json()
        assert value['tool_result']['status'] == 'completed', value['tool_result']['stderr_summary']
        assert value['tool_result']['synthetic'] is False
        assert requests and value['observation_count'] >= 1, value
        transport = value['tool_result']['network_transport']
        assert transport['requests'] == transport['responses'] == len(requests)
        assert transport['denied'] == 0
        with final_core.connect() as db:
            artifact = db.execute('SELECT uri FROM artifacts WHERE id=?', (value['artifact_id'],)).fetchone()
        artifact_text = Path(artifact['uri']).read_text()
        assert 'fieldwork:' not in artifact_text
        assert '_token' not in artifact_text
        assert '[REDACTED]@127.0.0.1' in artifact_text
        details = client.get(f"/api/v1/runs/{run['id']}/details").json()
        item = next(item for item in details['test_items'] if item['id'] == capability)
        assert item['status'] == 'completed'
        assert item['observation_count'] == value['observation_count']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        authority.__exit__(None, None, None)

from tests.test_final import client


def test_proxy_credentials_are_removed_from_stdout_stderr_and_synthetic_flag_is_preserved(monkeypatch):
    import base64
    from contextlib import contextmanager
    import network_tool_broker
    token = 'ephemeral-unit-nonce-9276'
    encoded = base64.b64encode(('fieldwork:' + token).encode()).decode()
    @contextmanager
    def broker(*args):
        yield 5555, {'requests': 0, 'responses': 0, 'denied': 0, 'errors': [], '_token': token}
    monkeypatch.setattr(network_tool_broker, 'scanner_broker', broker)
    monkeypatch.setattr(traditional_tools, '_load_run', lambda _: ({'id': 'fixture'},
        {'id': 'fixture-project', 'normalized_target': 'https://example.test', 'policy': {}}))
    monkeypatch.setattr(final_core, 'execution_policy_check', lambda _: {'allowed': True})
    monkeypatch.setattr(traditional_runtime, 'network_guard', lambda *args: None)
    monkeypatch.setattr(final_core, 'consume_run_budget', lambda *args: (True, 'allowed'))
    monkeypatch.setattr(capability_registry, 'execute', lambda *args, **kwargs:
        capability_registry.ToolResultEnvelope('katana', 'completed', 0, token + encoded, token + encoded, synthetic=True))
    captured = []
    def persist(*args, **kwargs):
        captured.append(args[2])
        return {'status': args[2].status}
    monkeypatch.setattr(traditional_tools, '_persist_result', persist)
    assert traditional_tools.run_capability('fixture', 'katana', None, 10)['status'] == 'failed'
    value = captured[0]
    assert value.synthetic is True
    assert token not in value.stdout + value.stderr
    assert encoded not in value.stdout + value.stderr
    assert 'no_observed_response' in value.stderr
