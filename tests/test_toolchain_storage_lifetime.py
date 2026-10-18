"""Cancellation must not release storage while a writer thread is alive."""
import asyncio
import threading

import pytest

import final_core
import traditional_tools
import web3_analysis
import web3_lab
from tests.test_storage_access import probe
from tests.test_web3_pipeline_routing import client, contract


def test_toolchain_thread_keeps_access_after_waiter_cancel(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', root)
    entered, release = threading.Event(), threading.Event()
    def write():
        entered.set()
        assert release.wait(10)
        (root / 'late-artifact').write_text('completed')
    async def scenario():
        task = asyncio.create_task(traditional_tools._storage_thread_call(write))
        while not entered.is_set():
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            assert 'storage_in_use' in probe(root, True).stderr
        finally:
            release.set()
    asyncio.run(scenario())
    assert (root / 'late-artifact').read_text() == 'completed'
    assert probe(root, True).returncode == 0


@pytest.mark.parametrize('cancel_at', ['fork_start', 'analysis'])
def test_cancelled_pipeline_cleans_late_fork_and_keeps_writer_lock(contract, monkeypatch, cancel_at):
    run_id, source = contract
    root = final_core.LOCAL_DATA_ROOT.resolve()
    entered, release = threading.Event(), threading.Event()
    stopped = []
    def wait():
        entered.set()
        assert release.wait(10)
        (root / 'late-write').write_text('completed')
    def fork(*args):
        if cancel_at == 'fork_start':
            wait()
        return {'id': 'owned-fork', 'chain_id': 1, 'fork_block': 42}
    def analyze(*args):
        if cancel_at == 'analysis':
            wait()
    async def tools(*args, **kwargs):
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='running' WHERE id=?", (run_id,))
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', fork)
    monkeypatch.setattr(web3_lab, 'stop_local_fork', lambda identity: stopped.append(identity))
    monkeypatch.setattr(traditional_tools, 'execute_toolchain', tools)
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', analyze)
    async def scenario():
        task = asyncio.create_task(traditional_tools.execute_web3_source_pipeline(
            run_id, traditional_tools.TraditionalToolchainInput(source_path=str(source)), 'https://rpc.invalid', 42, 1))
        while not entered.is_set():
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            assert 'storage_in_use' in probe(root, True).stderr
        finally:
            release.set()
    asyncio.run(scenario())
    assert stopped == ['owned-fork']
    assert (root / 'late-write').read_text() == 'completed'
    assert probe(root, True).returncode == 0


def test_fork_ownership_holds_access_until_cleanup(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', root)
    calls = []
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', lambda *args: {'id': 'owned'})
    monkeypatch.setattr(web3_lab, 'stop_local_fork', lambda identity: calls.append(identity))
    lease = traditional_tools._ForkLease()
    assert lease.start('engagement', 'https://rpc.invalid')['id'] == 'owned'
    assert 'storage_in_use' in probe(root, True).stderr
    lease.close()
    lease.close()
    assert calls == ['owned']
    assert probe(root, True).returncode == 0
    with pytest.raises(RuntimeError, match='cancelled'):
        lease.start('engagement', 'https://rpc.invalid')


def test_fork_cleanup_error_still_releases_access(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(final_core, 'LOCAL_DATA_ROOT', root)
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', lambda *args: {'id': 'owned'})
    def fail(*args):
        raise RuntimeError('cleanup fixture failed')
    monkeypatch.setattr(web3_lab, 'stop_local_fork', fail)
    lease = traditional_tools._ForkLease()
    lease.start('engagement', 'https://rpc.invalid')
    with pytest.raises(RuntimeError, match='cleanup fixture'):
        lease.close()
    assert probe(root, True).returncode == 0
