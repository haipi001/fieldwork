"""Single-tool HTTP requests share ownership with background toolchains."""
import asyncio
import threading

import pytest
import capability_registry
import final_core
import traditional_tools
from tests.test_repository_pipeline_control import client, repository, set_state, state


@pytest.mark.parametrize('control', [None, 'paused', 'stopped'])
def test_single_tool_waits_for_background_and_rechecks_control(client, repository, monkeypatch, control):
    run_id = repository
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()
    mutex = threading.Lock()
    calls = []
    active = 0
    maximum = 0
    def tool(*args, **kwargs):
        nonlocal active, maximum
        with mutex:
            active += 1
            maximum = max(maximum, active)
            calls.append('tool')
            first = len(calls) == 1
        try:
            if first:
                entered.set()
                assert release.wait(10)
            return {'observation_count': 0, 'observation_ids': [], 'subjects': [],
                    'tool_result': {'status': 'failed'}}
        finally:
            with mutex:
                active -= 1
    monkeypatch.setattr(capability_registry, 'inventory', lambda: [{'id': 'semgrep', 'available': True}])
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    monkeypatch.setattr(final_core, 'correlate_observations', lambda _: {'count': 0})
    original = traditional_tools._serialized_single_tool
    async def observed(*args, **kwargs):
        requested.set()
        return await original(*args, **kwargs)
    monkeypatch.setattr(traditional_tools, '_serialized_single_tool', observed)
    async def scenario():
        first = asyncio.create_task(traditional_tools.execute_toolchain(
            run_id, traditional_tools.TraditionalToolchainInput(include_code=True)))
        while not entered.is_set():
            await asyncio.sleep(.01)
        request = asyncio.create_task(asyncio.to_thread(client.post,
            f'/api/v1/traditional/runs/{run_id}/tools/run', json={'capability': 'semgrep'}))
        while not requested.is_set():
            await asyncio.sleep(.01)
        try:
            await asyncio.sleep(.1)
            assert calls == ['tool']
            if control:
                set_state(run_id, control)
        finally:
            release.set()
        await first
        response = await request
        assert response.status_code == (409 if control else 200), response.text
    asyncio.run(scenario())
    assert maximum == 1
    assert len(calls) == (1 if control else 2)
    assert state(run_id) == (control or 'completed')


def test_explicit_single_tool_on_already_paused_run_remains_available(client, repository, monkeypatch):
    run_id = repository
    set_state(run_id, 'paused')
    calls = []
    def tool(*args, **kwargs):
        calls.append('tool')
        return {'observation_ids': [], 'tool_result': {'status': 'failed'}}
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    response = client.post(f'/api/v1/traditional/runs/{run_id}/tools/run', json={'capability': 'semgrep'})
    assert response.status_code == 200, response.text
    assert calls == ['tool'] and state(run_id) == 'paused'


def test_cancelled_single_tool_waiter_keeps_ownership_through_coverage(repository, monkeypatch):
    import run_execution
    run_id = repository
    entered, release = threading.Event(), threading.Event()
    calls = []
    def tool(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return {'observation_ids': [], 'tool_result': {'status': 'failed'}}
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    @run_execution.serialize_run
    async def next_execution(run_id):
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM coverage_v2 WHERE run_id=?', (run_id,)).fetchone()[0] == 1
        calls.append('next')
    async def scenario():
        first = asyncio.create_task(traditional_tools.run_traditional_tool(
            run_id, traditional_tools.TraditionalToolInput(capability='semgrep')))
        while not entered.is_set():
            await asyncio.sleep(.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(next_execution(run_id))
        try:
            await asyncio.sleep(.1)
            assert not calls
        finally:
            release.set()
        await second
    asyncio.run(scenario())
    assert calls == ['next']


def test_busy_single_tool_returns_clear_conflict_without_tool_or_budget(client, repository, monkeypatch):
    import run_execution
    run_id = repository
    calls = []
    monkeypatch.setattr(traditional_tools, 'run_capability', lambda *args, **kwargs: calls.append('unexpected'))
    with final_core.connect() as db:
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',
                   (run_id, 100, 0, 10, 0, 1000000, 0, final_core.utcnow()))
    @run_execution.serialize_run
    async def hold(run_id):
        response = await asyncio.to_thread(client.post,
            f'/api/v1/traditional/runs/{run_id}/tools/run', json={'capability': 'semgrep'})
        assert response.status_code == 409, response.text
        assert '正在执行' in response.json()['detail']
    asyncio.run(hold(run_id))
    assert calls == []
    with final_core.connect() as db:
        assert db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?', (run_id,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM artifacts WHERE run_id=?', (run_id,)).fetchone()[0] == 0
    # The rejected waiting request must not leave ownership behind.
    @run_execution.serialize_run
    async def next_execution(run_id):
        calls.append('next')
    asyncio.run(next_execution(run_id))
    assert calls == ['next']
