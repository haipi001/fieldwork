"""Small local secret-reference store for V5 runtime providers.

Only opaque references enter SQLite.  Secret values live in owner-only files
and are never returned by runtime APIs.
"""
from __future__ import annotations

import os
import re
import stat
import uuid
from pathlib import Path


REFERENCE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
MAX_SECRET_BYTES = 16_384


def secret_root() -> Path:
    return Path(__file__).resolve().parent / "data" / "runtime-secrets"


def _path(reference: str) -> Path:
    if not REFERENCE.fullmatch(reference):
        raise ValueError("invalid secret reference")
    return secret_root() / reference


def put(reference: str, value: str) -> None:
    encoded = value.encode()
    if not encoded or len(encoded) > MAX_SECRET_BYTES or "\x00" in value:
        raise ValueError("invalid secret value")
    root = secret_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_details = root.lstat()
    if not stat.S_ISDIR(root_details.st_mode) or stat.S_ISLNK(root_details.st_mode) or root_details.st_uid != os.getuid():
        raise PermissionError("secret root is not an owner-controlled directory")
    root.chmod(0o700)
    destination = _path(reference)
    temporary = root / f".{reference}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    destination.chmod(0o600)


def get(reference: str) -> str | None:
    root = secret_root()
    try:
        root_details = root.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(root_details.st_mode) or stat.S_ISLNK(root_details.st_mode) or root_details.st_uid != os.getuid():
        raise PermissionError("secret root is not an owner-controlled directory")
    path = _path(reference)
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise PermissionError("secret reference is not a regular file")
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
        raise PermissionError("secret file ownership or permissions are unsafe")
    if details.st_size > MAX_SECRET_BYTES:
        raise ValueError("secret value exceeds size limit")
    return path.read_text()


def delete(reference: str) -> None:
    path = _path(reference)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
