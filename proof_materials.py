"""Read bounded proof bytes without following links or accepting changed files."""
import os
from pathlib import Path
import stat

from storage_migration import _open_directory, _plain_directory


def read_proof_file(path, limit):
    path = Path(path)
    parent = _open_directory(_plain_directory(path.parent))
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('proof_file_invalid_or_too_large')
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > limit or len(raw) != before.st_size or identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('proof_file_changed')
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
