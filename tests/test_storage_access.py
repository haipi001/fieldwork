import os
from pathlib import Path
import subprocess
import sys

import pytest
from storage_access import storage_access


def probe(root, maintenance):
    code = 'from storage_access import storage_access\nimport sys\nwith storage_access(sys.argv[1],maintenance=sys.argv[2]=="1"):\n print("entered")'
    return subprocess.run([sys.executable, '-c', code, str(root), str(int(maintenance))],
                          capture_output=True, text=True, timeout=10)


def test_live_services_share_access_and_block_maintenance(tmp_path):
    root = tmp_path.resolve()
    with storage_access(root):
        assert probe(root, False).returncode == 0
        denied = probe(root, True)
        assert denied.returncode != 0 and 'storage_in_use' in denied.stderr
    assert probe(root, True).returncode == 0


def test_maintenance_blocks_service_and_other_maintenance(tmp_path):
    root = tmp_path.resolve()
    with storage_access(root, maintenance=True):
        denied = probe(root, False)
        assert denied.returncode != 0 and 'storage_maintenance_active' in denied.stderr
        assert probe(root, True).returncode != 0
    assert probe(root, False).returncode == 0


@pytest.mark.parametrize('kind', ['link', 'hardlink', 'public', 'fifo'])
def test_unsafe_lock_file_rejected(tmp_path, kind):
    root = tmp_path.resolve()
    lock = root / '.storage-access.lock'
    if kind in ('link', 'hardlink'):
        other = root / 'other'; other.write_text('preserved')
        if kind == 'link': lock.symlink_to(other)
        else: os.link(other, lock)
    elif kind == 'fifo': os.mkfifo(lock)
    else: lock.write_text(''); lock.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        with storage_access(root): pytest.fail('unsafe lock accepted')
    if kind in ('link', 'hardlink'): assert other.read_text() == 'preserved'


def test_process_exit_releases_lock_without_deleting_file(tmp_path):
    root = tmp_path.resolve()
    code = '''from storage_access import storage_access
import sys
with storage_access(sys.argv[1], maintenance=True):
 print('ready', flush=True)
 sys.stdin.read()
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(root)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        assert probe(root, False).returncode != 0
        child.communicate(timeout=10)
        assert child.returncode == 0
        assert (root / '.storage-access.lock').is_file()
        assert probe(root, False).returncode == 0
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)


def test_app_refuses_startup_before_database_creation_during_maintenance(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    code = '''from fastapi.testclient import TestClient
import app
with TestClient(app.app):
 pass
'''
    with storage_access(root, maintenance=True):
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, FIELDWORK_DATA_DIRECTORY=str(root),
                                         FIELDWORK_DISABLE_AGENT_MONITOR='1', FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER='1'))
    assert result.returncode != 0 and 'storage_maintenance_active' in result.stderr
    assert not (root / 'src_control.db').exists()


def test_cancelled_async_waiter_keeps_thread_lock_until_work_finishes(tmp_path):
    import asyncio
    import threading
    from storage_access import storage_call
    root = tmp_path.resolve()
    entered, release = threading.Event(), threading.Event()
    def write_later():
        entered.set()
        assert release.wait(10)
        (root / 'completed').write_text('done')
    async def scenario():
        task = asyncio.create_task(asyncio.to_thread(storage_call, root, write_later))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            denied = probe(root, True)
            assert denied.returncode != 0 and 'storage_in_use' in denied.stderr
        finally:
            release.set()
    asyncio.run(scenario())  # joins its default executor, including cancelled work
    assert (root / 'completed').read_text() == 'done'
    assert probe(root, True).returncode == 0
