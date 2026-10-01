"""Assemble an installed root service's existing audit pairing. No elevation."""
import base64
import fcntl
import hashlib
import hmac
import os
import re
import stat
from pathlib import Path

import native_ai_keys
from native_ai_registration import register_installation
from native_ai_launcher import verify_installed_collector
from native_ai_forwarder import LoopbackTransport
from native_ai_queue import EncryptedPendingQueue
from native_ai_pipeline import NativePipeline
from native_ai_session import NativeCollectorSession

QUEUE_DIRECTORY=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/queues')


def _queue_directory(scope):
    for path in [QUEUE_DIRECTORY,*QUEUE_DIRECTORY.parents]:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('原生队列目录不受管理员权限保护')
    fd=os.open(QUEUE_DIRECTORY,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
            raise PermissionError('原生队列根目录必须由管理员持有且权限为 0700')
        try:os.mkdir(scope,0o700,dir_fd=fd);os.fsync(fd)
        except FileExistsError:pass
        child=os.stat(scope,dir_fd=fd,follow_symlinks=False)
        if not stat.S_ISDIR(child.st_mode) or child.st_uid!=0 or stat.S_IMODE(child.st_mode)!=0o700:
            raise PermissionError('审计队列目录的所有权、权限或类型无效')
    finally:os.close(fd)
    return QUEUE_DIRECTORY/scope


class NativeServiceRuntime:
    def __init__(self,queue,pipeline,session,lease,sequence_queue=None):
        self.queue=queue;self.pipeline=pipeline;self.session=session;self.lease=lease;self.closed=False
        self.sequence_queue=sequence_queue

    def start(self):
        if self.closed:raise ValueError('原生服务会话已关闭')
        return self.session.start()

    def tick(self):
        if self.closed:raise ValueError('原生服务会话已关闭')
        return self.session.tick()

    def drain(self,grace=3):
        if self.closed:return self.session.health()
        self.session.quiesce(grace)
        return self.close(grace)

    def close(self,grace=3):
        if self.closed:return self.session.health()
        result=self.session.stop(grace)
        # Keep the queue and lease available if persistence failed; caller can retry.
        self.pipeline.persist()
        if self.session.error=='stop_persistence_failure':self.session.error=None
        result={**result,**self.session.health(),'unsaved_records':len(self.pipeline.records)}
        self.queue.close();os.close(self.lease);self.lease=None;self.closed=True
        if self.sequence_queue is not None:self.sequence_queue.close()
        return result

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def prepare(*,team_id,audit_id,collector_id,session_id,public_key,base_url='http://127.0.0.1:8000'):
    """Pairing must come from the installed service's authorized control channel.

    Prepares durable state without starting a producer or sending any HTTP request.
    """
    native_ai_keys._root_required()
    for value in (audit_id,collector_id):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
            raise ValueError('原生审计及采集器标识无效')
    if not isinstance(session_id,str) or not 1<=len(session_id)<=256 or any(ord(c)<32 for c in session_id):
        raise ValueError('原生会话标识无效')
    transport=LoopbackTransport(base_url)
    try:supplied=base64.b64decode(public_key,validate=True)
    except (ValueError,TypeError):raise ValueError('配对公钥无效') from None
    if len(supplied)!=32:raise ValueError('配对公钥无效')
    verify_installed_collector(team_id)
    keys=native_ai_keys.load_or_create()
    if not hmac.compare_digest(supplied,base64.b64decode(keys.public_key_base64())):
        raise ValueError('网页配对公钥与已安装服务身份不一致')
    registration=register_installation(team_id)
    if not hmac.compare_digest(supplied,base64.b64decode(registration['public_key'])):
        raise ValueError('服务公共登记发生变化')
    scope=hashlib.sha256((audit_id+'\0'+collector_id+'\0'+session_id).encode()).hexdigest()
    directory=_queue_directory(scope)
    queue=EncryptedPendingQueue(directory,keys.queue_key,audit_id=audit_id,collector_id=collector_id)
    lease=None;sequence_queue=None
    try:
        counter_directory=_queue_directory('sequence-'+hashlib.sha256(collector_id.encode()).hexdigest())
        sequence_queue=EncryptedPendingQueue(counter_directory,keys.queue_key,audit_id='collector-sequence',collector_id=collector_id)
        lease=os.open('service.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=sequence_queue.fd)
        info=os.fstat(lease)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:
            raise PermissionError('原生服务锁文件无效')
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('该原生采集器已有服务正在使用') from None
        pipeline=NativePipeline(queue=queue,transport=transport,private_key=keys.signing_key,
            audit_id=audit_id,collector_id=collector_id,session_id=session_id,sequence_queue=sequence_queue)
        return NativeServiceRuntime(queue,pipeline,NativeCollectorSession(pipeline,team_id),lease,sequence_queue)
    except BaseException:
        if lease is not None:os.close(lease)
        if sequence_queue is not None:sequence_queue.close()
        queue.close();raise
