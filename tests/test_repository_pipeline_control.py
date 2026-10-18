"""Repository and toolchain control transitions; Git/analyzers are substitutes."""
import asyncio
import subprocess

import pytest
import capability_registry
import final_core
import traditional_tools
import web3_analysis
from tests.test_final import client, create_ready


@pytest.fixture
def repository(client, tmp_path, monkeypatch):
    engagement = create_ready(client, mode='web3', target='https://code.example.test/org/repo.git')
    run_id = 'repository-control'
    with final_core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, engagement['id'], 'web3', engagement['current_scope_snapshot_id'],
                    engagement['current_policy_id'], 'queued', 'target', 0, None, None, None, None, final_core.utcnow()))
        db.execute('INSERT INTO run_configs_v2 VALUES(?,?,?)', (run_id, '{}', final_core.utcnow()))
    monkeypatch.setattr(traditional_tools, 'REPOSITORY_ROOT', tmp_path / 'repositories')
    return run_id


def set_state(run_id, state):
    with final_core.connect() as db:
        db.execute('UPDATE analysis_runs SET status=? WHERE id=?', (state, run_id))


def state(run_id):
    with final_core.connect() as db:
        return db.execute('SELECT status FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]


@pytest.mark.parametrize('stage', ['clone', 'commit', 'tools', 'analysis'])
@pytest.mark.parametrize('terminal', ['paused', 'stopped', 'budget_exhausted', 'timeout'])
def test_repository_stops_at_control_boundary(repository, monkeypatch, stage, terminal):
    run_id = repository
    calls = []
    async def thread(function, args, **kwargs):
        current = 'clone' if 'clone' in args else 'commit'
        calls.append(current)
        if current == stage:
            set_state(run_id, terminal)
        return subprocess.CompletedProcess(args, 0, stdout='a' * 40, stderr='')
    async def tools(*args, **kwargs):
        calls.append('tools')
        if stage == 'tools':
            set_state(run_id, terminal)
    def analyze(*args):
        calls.append('analysis')
        if stage == 'analysis':
            set_state(run_id, terminal)
    async def dispatch(function, *args, **kwargs):
        if function is subprocess.run:
            return await thread(function, *args, **kwargs)
        return function(*args, **kwargs)
    monkeypatch.setattr(traditional_tools, '_storage_thread_call', dispatch)
    monkeypatch.setattr(traditional_tools, 'execute_toolchain', tools)
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', analyze)
    monkeypatch.setattr(final_core, 'correlate_observations', lambda _: pytest.fail('must not correlate after control transition'))
    asyncio.run(traditional_tools.checkout_and_execute_repository(
        run_id, 'https://code.example.test/org/repo.git', traditional_tools.TraditionalToolchainInput()))
    assert state(run_id) == terminal
    expected = ['clone', 'commit', 'tools', 'analysis']
    assert calls == expected[:expected.index(stage) + 1]


@pytest.mark.parametrize('failure', ['nonzero', 'empty', 'timeout', 'missing_git'])
def test_commit_failure_has_explicit_terminal(repository, monkeypatch, failure):
    run_id = repository
    async def dispatch(function, args, **kwargs):
        if 'clone' in args:
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 15)
        if failure == 'missing_git':
            raise OSError('fixture missing Git')
        return subprocess.CompletedProcess(args, 1 if failure == 'nonzero' else 0, stdout='', stderr='')
    monkeypatch.setattr(traditional_tools, '_storage_thread_call', dispatch)
    asyncio.run(traditional_tools.checkout_and_execute_repository(
        run_id, 'https://code.example.test/org/repo.git', traditional_tools.TraditionalToolchainInput()))
    assert state(run_id) == 'failed'


@pytest.mark.parametrize('terminal', ['paused', 'stopped'])
def test_final_code_tool_control_does_not_emit_completion(repository, monkeypatch, terminal):
    run_id = repository
    def tool(*args, **kwargs):
        set_state(run_id, terminal)
        return {'observation_count': 0, 'observation_ids': [], 'subjects': [], 'tool_result': {'status': 'failed'}}
    monkeypatch.setattr(capability_registry, 'inventory', lambda: [{'id': 'semgrep', 'available': True}])
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    monkeypatch.setattr(final_core, 'correlate_observations', lambda _: pytest.fail('must not correlate after pause'))
    asyncio.run(traditional_tools.execute_toolchain(run_id, traditional_tools.TraditionalToolchainInput(include_code=True)))
    assert state(run_id) == terminal
    with final_core.connect() as db:
        assert db.execute("SELECT count(*) FROM run_events_v2 WHERE run_id=? AND kind='run.completed'", (run_id,)).fetchone()[0] == 0


@pytest.mark.parametrize('initial', ['paused', 'completed'])
def test_explicit_toolchain_start_can_requeue_existing_run(client, repository, monkeypatch, initial):
    run_id = repository
    set_state(run_id, initial)
    calls = []
    async def tools(*args, **kwargs):
        calls.append(state(run_id))
    monkeypatch.setattr(traditional_tools, 'execute_toolchain', tools)
    response = client.post(f'/api/v1/traditional/runs/{run_id}/toolchain', json={'include_code': False})
    assert response.status_code == 202, response.text
    assert calls == ['queued']


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'paused_failure'])
def test_repository_analysis_success_and_failure_transitions(repository, monkeypatch, outcome):
    run_id = repository
    calls = []
    async def dispatch(function, *args, **kwargs):
        if function is subprocess.run:
            return subprocess.CompletedProcess(args[0], 0, stdout='a' * 40, stderr='')
        return function(*args, **kwargs)
    async def tools(*args, **kwargs):
        calls.append('tools')
    def analyze(*args):
        calls.append('analysis')
        if outcome == 'paused_failure':
            set_state(run_id, 'paused')
        if outcome != 'completed':
            raise RuntimeError('fixture analyzer failed')
    def correlate(*args):
        calls.append('correlate')
        return {'count': 0}
    monkeypatch.setattr(traditional_tools, '_storage_thread_call', dispatch)
    monkeypatch.setattr(traditional_tools, 'execute_toolchain', tools)
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', analyze)
    monkeypatch.setattr(final_core, 'correlate_observations', correlate)
    asyncio.run(traditional_tools.checkout_and_execute_repository(
        run_id, 'https://code.example.test/org/repo.git', traditional_tools.TraditionalToolchainInput()))
    assert state(run_id) == ('paused' if outcome == 'paused_failure' else outcome)
    assert ('correlate' in calls) == (outcome == 'completed')
    with final_core.connect() as db:
        complete = db.execute("SELECT count(*) FROM run_events_v2 WHERE run_id=? AND kind='run.completed'", (run_id,)).fetchone()[0]
    assert complete == (1 if outcome == 'completed' else 0)
