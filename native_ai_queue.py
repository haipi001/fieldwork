"""Encrypted single pending batch. Service owns key storage and exclusive access."""
import os
import secrets
import stat
import fcntl
import threading
from functools import wraps
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_BYTES = 2_000_000


def locked(method):
    @wraps(method)
    def operation(self,*args,**kwargs):
        with self.lock:
            fcntl.flock(self.fd,fcntl.LOCK_EX)
            try:return method(self,*args,**kwargs)
            finally:fcntl.flock(self.fd,fcntl.LOCK_UN)
    return operation


class EncryptedPendingQueue:
    def __init__(self, directory, key, *, audit_id, collector_id):
        if not isinstance(key,bytes) or len(key)!=32:
            raise ValueError('队列需要独立的 32 字节加密密钥')
        self.cipher=AESGCM(key)
        self.lock=threading.RLock()
        self.context=('fieldwork-native-pending/1\0'+audit_id+'\0'+collector_id).encode()
        path=Path(directory)
        path.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        info=os.fstat(self.fd)
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            self.close()
            raise ValueError('队列目录必须属于服务用户且权限为 0700')

    def close(self):
        if getattr(self,'fd',None) is not None:
            os.close(self.fd);self.fd=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    @locked
    def load(self):return self._load()

    def _load(self):
        try:fd=os.open('pending.enc',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self.fd)
        except FileNotFoundError:return None
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:
                raise ValueError('队列文件权限或类型无效')
            blob=source.read(MAX_BYTES+29)
            if len(blob)>MAX_BYTES+28 or len(blob)<28:
                raise ValueError('队列文件大小无效')
        try:return self.cipher.decrypt(blob[:12],blob[12:],self.context)
        except InvalidTag:raise ValueError('队列完整性、密钥或审计范围校验失败') from None

    @locked
    def save(self,request_bytes):
        if not isinstance(request_bytes,bytes) or not 1<=len(request_bytes)<=MAX_BYTES:
            raise ValueError('待提交批次大小无效')
        existing=self._load()
        if existing is not None:
            if existing==request_bytes:return
            raise ValueError('上一批尚未确认，不能覆盖')
        nonce=secrets.token_bytes(12)
        blob=nonce+self.cipher.encrypt(nonce,request_bytes,self.context)
        temporary='pending-'+secrets.token_hex(12)+'.tmp'
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
            with os.fdopen(fd,'wb') as target:
                target.write(blob);target.flush();os.fsync(target.fileno())
            # Link publishes without replacing an unacknowledged concurrent batch.
            os.link(temporary,'pending.enc',src_dir_fd=self.fd,dst_dir_fd=self.fd,follow_symlinks=False)
            os.fsync(self.fd)
        finally:
            try:os.unlink(temporary,dir_fd=self.fd)
            except FileNotFoundError:pass

    @locked
    def acknowledge(self,request_bytes):
        if self._load()!=request_bytes:
            raise ValueError('确认批次与持久队列不一致')
        os.unlink('pending.enc',dir_fd=self.fd)
        os.fsync(self.fd)
