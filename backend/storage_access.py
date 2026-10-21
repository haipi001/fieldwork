"""Cooperative storage access gate for service lifetimes and offline maintenance.

This does not freeze uncooperative programs or older Fieldwork processes.
The lock file must never be replaced or deleted while any participant runs.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from storage_migration import _open_directory, _plain_directory


def open_runtime_lock(parent,directory,name):
    """Retry only transient lock-file opens inside the same verified directory.

    No absolute-path fallback and no task replay. A replaced directory or a
    symlink remains an error; callers still verify the file and flock identity.
    """
    if name not in {'.storage-access.lock','.agent-attack-execution.lock'}:
        raise ValueError('unsupported_runtime_lock')
    pinned=os.fstat(parent)
    if not stat.S_ISDIR(pinned.st_mode):raise ValueError('runtime_lock_parent_not_directory')
    def verify():
        path=_plain_directory(directory)
        current=path.stat();handle=os.fstat(parent)
        if (current.st_dev,current.st_ino)!=(pinned.st_dev,pinned.st_ino) or (handle.st_dev,handle.st_ino)!=(pinned.st_dev,pinned.st_ino):
            raise RuntimeError('storage_directory_changed')
    for attempt in range(3):
        verify()
        try:
            descriptor=os.open(name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=parent)
        except FileNotFoundError:
            if attempt==2:raise
            continue
        try:verify()
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor


@contextmanager
def storage_access(directory, *, maintenance=False):
    directory = _plain_directory(Path(directory))
    parent = _open_directory(directory)
    descriptor = None
    try:
        descriptor = open_runtime_lock(parent,directory,'.storage-access.lock')
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError('invalid_storage_access_lock')
        mode = fcntl.LOCK_EX if maintenance else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('storage_in_use' if maintenance else 'storage_maintenance_active') from error
        current = os.stat('.storage-access.lock', dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise RuntimeError('storage_access_lock_changed')
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def storage_call(directory, function, *args, **kwargs):
    """Hold access in the executing thread even if its async caller is cancelled."""
    with storage_access(directory):
        return function(*args, **kwargs)
