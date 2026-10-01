"""Publish the public identity of an already installed privileged collector."""
import fcntl
import json
import os
import secrets
import stat

import native_ai_keys
from native_ai_binding import REGISTRY
from native_ai_launcher import verify_installed_collector,protected_binary,INSTALL_PATH


def _directory_fd():
    for path in [REGISTRY.parent,*REGISTRY.parent.parents]:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('公共登记目录不受管理员权限保护')
    fd=os.open(REGISTRY.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if info.st_uid!=0 or info.st_mode&0o022:
        os.close(fd);raise PermissionError('公共登记目录不受管理员权限保护')
    return fd


def _read(fd):
    source=os.open(REGISTRY.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    with os.fdopen(source,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid()
                or stat.S_IMODE(before.st_mode)!=0o644 or before.st_nlink!=1):
            raise PermissionError('公共登记文件的所有权、权限或类型无效')
        raw=stream.read(4097)
        if len(raw)>4096:raise ValueError('公共登记文件超过上限')
        identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        if identity(before)!=identity(os.fstat(stream.fileno())) or identity(before)!=identity(
                os.stat(REGISTRY.name,dir_fd=fd,follow_symlinks=False)):
            raise ValueError('读取期间公共登记发生变化')
    return json.loads(raw)


def register_installation(team_id):
    """Root-only, idempotent registration; no installation or key rotation."""
    native_ai_keys._root_required()
    binary_identity=verify_installed_collector(team_id)
    keys=native_ai_keys.load_or_create()
    expected={'schema':'fieldwork-native-binding/1','team_id':team_id,
        'public_key':keys.public_key_base64()}
    fd=_directory_fd();temporary=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        if protected_binary(INSTALL_PATH)!=binary_identity:
            raise ValueError('登记期间采集器安装发生变化')
        try:
            existing=_read(fd)
        except FileNotFoundError:existing=None
        if existing is not None:
            if existing!=expected:raise ValueError('公共登记与服务身份不一致，不能自动覆盖')
            return expected
        temporary='.public-'+secrets.token_hex(16)
        target=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=fd)
        with os.fdopen(target,'wb') as stream:
            os.fchmod(stream.fileno(),0o644)
            stream.write(json.dumps(expected,sort_keys=True,separators=(',',':')).encode())
            stream.flush();os.fsync(stream.fileno())
        try:os.link(temporary,REGISTRY.name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
        except FileExistsError:pass
        os.unlink(temporary,dir_fd=fd);temporary=None;os.fsync(fd)
        if _read(fd)!=expected:raise ValueError('公共登记发布发生冲突')
        return expected
    finally:
        if temporary is not None:
            try:os.unlink(temporary,dir_fd=fd)
            except FileNotFoundError:pass
        os.close(fd)
