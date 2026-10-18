"""Serialize each Run across processes, retaining ownership for surviving threads.

Lock files must not be replaced or deleted while participating services run.
"""
import asyncio
import contextvars
import fcntl
import functools
import hashlib
import os
from pathlib import Path
import stat
import threading

from storage_access import storage_access, storage_call
from storage_migration import _open_directory, _plain_directory

_CURRENT = contextvars.ContextVar('fieldwork_run_execution', default=None)


class RunExecutionBusy(RuntimeError):
    pass


class _Lease:
    def __init__(self, root, run_id):
        self.key = (str(root), run_id)
        self.gate = storage_access(root)
        self.descriptor = None
        self.references = 1
        self.mutex = threading.Lock()
        self.gate_entered = False

    async def acquire(self, root, run_id, wait_seconds=None):
        self.gate.__enter__()
        self.gate_entered = True
        directory = Path(root) / '.run-execution'
        directory.mkdir(mode=0o700, exist_ok=True)
        parent = _open_directory(_plain_directory(directory))
        try:
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('invalid_run_execution_directory')
            name = hashlib.sha256(run_id.encode()).hexdigest() + '.lock'
            self.descriptor = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                      0o600, dir_fd=parent)
            info = os.fstat(self.descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError('invalid_run_execution_lock')
            deadline = asyncio.get_running_loop().time() + wait_seconds if wait_seconds is not None else None
            while True:
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    raise RunExecutionBusy('run_execution_busy')
                try:
                    fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                        raise RuntimeError('run_execution_lock_changed')
                    return
                except BlockingIOError:
                    await asyncio.sleep(.05)
        finally:
            os.close(parent)

    def retain(self):
        with self.mutex:
            if not self.references:
                raise RuntimeError('run_execution_closed')
            self.references += 1

    def matches(self, key):
        with self.mutex:
            return bool(self.references and self.key == key)

    def release(self):
        with self.mutex:
            self.references -= 1
            if self.references:
                return
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None
            if self.gate_entered:
                self.gate.__exit__(None, None, None)
                self.gate_entered = False


def serialize_run(function=None, *, wait_seconds=None, reuse_parent=True):
    if function is None:
        return lambda target: serialize_run(target, wait_seconds=wait_seconds, reuse_parent=reuse_parent)
    @functools.wraps(function)
    async def wrapped(run_id, *args, **kwargs):
        import final_core
        root = final_core.LOCAL_DATA_ROOT.resolve()
        current = _CURRENT.get()
        if reuse_parent and current is not None and current.matches((str(root), run_id)):
            return await function(run_id, *args, **kwargs)
        lease = _Lease(root, run_id)
        token = None
        try:
            await lease.acquire(root, run_id, wait_seconds)
            token = _CURRENT.set(lease)
            return await function(run_id, *args, **kwargs)
        finally:
            if token is not None:
                _CURRENT.reset(token)
            lease.release()
    return wrapped


async def storage_thread(root, function, *args, **kwargs):
    lease = _CURRENT.get()
    context = contextvars.copy_context()
    if lease is not None:
        lease.retain()
    def execute():
        try:
            return storage_call(root, function, *args, **kwargs)
        finally:
            if lease is not None:
                lease.release()
    try:
        future = asyncio.get_running_loop().run_in_executor(None, context.run, execute)
    except BaseException:
        if lease is not None:
            lease.release()
        raise
    # Cancelling the coroutine must not cancel a queued executor job and strand
    # its retained lease. The thread releases ownership even after its waiter exits.
    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.shield(future)
