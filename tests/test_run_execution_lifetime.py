"""A resumed Run must not overlap its still-running previous executor."""
import asyncio
import threading

import pytest
import capability_registry
import final_core
import traditional_tools
from tests.test_repository_pipeline_control import client, repository, set_state, state


def test_rapid_pause_resume_does_not_overlap_tool_executors(repository, monkeypatch):
    run_id = repository
    entered, release = threading.Event(), threading.Event()
    calls = []
    def tool(*args, **kwargs):
        calls.append('tool')
        entered.set()
        assert release.wait(10)
        return {'observation_count': 0, 'observation_ids': [], 'subjects': [], 'tool_result': {'status': 'failed'}}
    monkeypatch.setattr(capability_registry, 'inventory', lambda: [{'id': 'semgrep', 'available': True}])
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    monkeypatch.setattr(final_core, 'correlate_observations', lambda _: {'count': 0})
    async def scenario():
        body = traditional_tools.TraditionalToolchainInput(include_code=True)
        first = asyncio.create_task(traditional_tools.execute_toolchain(run_id, body))
        while not entered.is_set():
            await asyncio.sleep(.01)
        set_state(run_id, 'paused')
        set_state(run_id, 'running')
        second = asyncio.create_task(traditional_tools.execute_toolchain(run_id, body))
        try:
            await asyncio.sleep(.15)
            assert calls == ['tool']
        finally:
            release.set()
            await asyncio.gather(first, second)
    asyncio.run(scenario())
    assert state(run_id) == 'completed'


def test_cancelled_executor_keeps_run_ownership_until_thread_finishes(tmp_path, monkeypatch):
    import run_execution
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    entered, release = threading.Event(), threading.Event()
    calls = []
    def write():
        entered.set()
        assert release.wait(10)
        calls.append('thread_finished')
    @run_execution.serialize_run
    async def execute(run_id, first=False):
        calls.append('first' if first else 'second')
        if first:
            await run_execution.storage_thread(tmp_path.resolve(), write)
    async def scenario():
        first = asyncio.create_task(execute('same-run', True))
        while not entered.is_set():
            await asyncio.sleep(.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(execute('same-run'))
        try:
            await asyncio.sleep(.1)
            assert calls == ['first']
        finally:
            release.set()
        await second
    asyncio.run(scenario())
    assert calls == ['first', 'thread_finished', 'second']


def test_independent_runs_can_execute_concurrently(tmp_path, monkeypatch):
    import run_execution
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    entered = set()
    release = asyncio.Event()
    @run_execution.serialize_run
    async def execute(run_id):
        entered.add(run_id)
        await release.wait()
    async def scenario():
        tasks = [asyncio.create_task(execute(name)) for name in ['first-run', 'second-run']]
        try:
            async with asyncio.timeout(2):
                while len(entered) < 2:
                    await asyncio.sleep(.01)
        finally:
            release.set()
            await asyncio.gather(*tasks)
    asyncio.run(scenario())
    assert entered == {'first-run', 'second-run'}


def test_run_ownership_is_enforced_across_processes(tmp_path, monkeypatch):
    import subprocess
    import sys
    import run_execution
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    code = '''import asyncio,sys
from pathlib import Path
import final_core
from run_execution import serialize_run
final_core.LOCAL_DATA_ROOT=Path(sys.argv[1])
@serialize_run
async def execute(run_id):
 print('entered',flush=True)
async def main():
 try:
  await asyncio.wait_for(execute('shared-run'),.2)
 except asyncio.TimeoutError:
  print('blocked',flush=True)
asyncio.run(main())
'''
    @run_execution.serialize_run
    async def execute(run_id):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path.resolve())],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'blocked'
    asyncio.run(execute('shared-run'))
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path.resolve())],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'entered'


@pytest.mark.parametrize('unsafe', ['link', 'public'])
def test_unsafe_execution_lock_is_refused_and_storage_released(tmp_path, monkeypatch, unsafe):
    import hashlib
    import run_execution
    from tests.test_storage_access import probe
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    directory = tmp_path / '.run-execution'; directory.mkdir(mode=0o700)
    path = directory / (hashlib.sha256(b'shared-run').hexdigest() + '.lock')
    if unsafe == 'link':
        other = tmp_path / 'other'; other.write_text('preserved')
        path.symlink_to(other)
    else:
        path.write_text(''); path.chmod(0o644)
    @run_execution.serialize_run
    async def execute(run_id):
        pytest.fail('unsafe execution lock accepted')
    with pytest.raises((ValueError, OSError)):
        asyncio.run(execute('shared-run'))
    assert probe(tmp_path.resolve(), True).returncode == 0


def test_cancelled_waiter_does_not_release_active_run(tmp_path, monkeypatch):
    import run_execution
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    @run_execution.serialize_run
    async def execute(run_id):
        calls.append('entered')
        started.set()
        await release.wait()
    async def scenario():
        first = asyncio.create_task(execute('shared-run'))
        await started.wait()
        second = asyncio.create_task(execute('shared-run'))
        await asyncio.sleep(.1)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        third = asyncio.create_task(execute('shared-run'))
        try:
            await asyncio.sleep(.1)
            assert calls == ['entered']
        finally:
            release.set()
        await asyncio.gather(first, third)
    asyncio.run(scenario())
    assert calls == ['entered', 'entered']


def test_dead_owner_releases_run_without_deleting_lock_file(tmp_path, monkeypatch):
    import select
    import subprocess
    import sys
    import run_execution
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', tmp_path.resolve())
    code = '''import asyncio,sys
from pathlib import Path
import final_core
from run_execution import serialize_run
final_core.LOCAL_DATA_ROOT=Path(sys.argv[1])
@serialize_run
async def execute(run_id):
 print('held',flush=True)
 sys.stdin.read()
asyncio.run(execute('shared-run'))
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(tmp_path.resolve())],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == 'held'
        child.kill()
        child.wait(timeout=10)
        entered = []
        @run_execution.serialize_run
        async def execute(run_id):
            entered.append(run_id)
        async def scenario():
            await asyncio.wait_for(execute('shared-run'), 2)
        asyncio.run(scenario())
        assert entered == ['shared-run']
        assert len(list((tmp_path / '.run-execution').glob('*.lock'))) == 1
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)
        child.stdin.close(); child.stdout.close(); child.stderr.close()
