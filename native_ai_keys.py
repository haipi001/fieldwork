"""Persistent keys for an installed root service; no elevation or installation."""
import base64
import fcntl
import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_DIRECTORY=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/keys')


def _root_required():
    if os.geteuid()!=0:raise PermissionError('原生服务密钥只能由已安装的管理员服务读取')


def _directory_fd():
    for path in [KEY_DIRECTORY,*KEY_DIRECTORY.parents]:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('密钥目录或父目录不受管理员权限保护')
    fd=os.open(KEY_DIRECTORY,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
        os.close(fd);raise PermissionError('密钥目录必须由管理员持有且权限为 0700')
    return fd


@dataclass(repr=False)
class NativeServiceKeys:
    signing_key: Ed25519PrivateKey=field(repr=False)
    queue_key: bytes=field(repr=False)

    def public_key_base64(self):
        return base64.b64encode(self.signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()


def _read(fd):
    source=os.open('keys.bin',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    with os.fdopen(source,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
            raise PermissionError('原生密钥文件的所有权、权限或类型无效')
        raw=stream.read(65)
        if len(raw)!=64:raise ValueError('原生密钥文件损坏，不能自动替换')
        after=os.fstat(stream.fileno())
        current=os.stat('keys.bin',dir_fd=fd,follow_symlinks=False)
        identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)
        if identity(info)!=identity(after) or identity(info)!=identity(current):
            raise ValueError('读取期间原生密钥发生变化')
    return NativeServiceKeys(Ed25519PrivateKey.from_private_bytes(raw[:32]),raw[32:])


def load_or_create():
    """Initialize once inside an existing protected installation; never rotate."""
    _root_required()
    fd=_directory_fd()
    temporary=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        try:return _read(fd)
        except FileNotFoundError:pass
        temporary='.keys-'+secrets.token_hex(16)
        target=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(target,'wb') as stream:
            os.fchmod(stream.fileno(),0o600)
            stream.write(secrets.token_bytes(32)+secrets.token_bytes(32))
            stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,'keys.bin',src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        except FileExistsError:pass
        os.unlink(temporary,dir_fd=fd);temporary=None
        os.fsync(fd)
        return _read(fd)
    finally:
        if temporary is not None:
            try:os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:pass
        os.close(fd)
