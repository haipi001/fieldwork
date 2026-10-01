"""Protected configuration and listener for an installed macOS root service."""
import errno
import fcntl
import json
import math
import os
import pwd
import select
import socket
import stat
import threading
import signal
from pathlib import Path

import native_ai_ipc as ipc
import native_ai_keys
from native_ai_control import NativeControl
from native_ai_launcher import protected_file

CONFIG_PATH=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/service.json')
SOCKET_PATH=ipc.SOCKET_PATH


def load_control():
    native_ai_keys._root_required()
    before=protected_file(CONFIG_PATH)
    fd=os.open(CONFIG_PATH,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1
                or (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=before):
            raise PermissionError('服务配置的所有权、权限或类型无效')
        raw=source.read(4097)
        if len(raw)>4096:raise ValueError('服务配置超过上限')
    if protected_file(CONFIG_PATH)!=before:raise ValueError('读取期间服务配置发生变化')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('服务配置包含重复字段')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=pairs)
    if (not isinstance(value,dict) or set(value)!={'schema','app_uid','team_id','base_url'}
            or value['schema']!='fieldwork-native-service/1' or not isinstance(value['base_url'],str)):
        raise ValueError('服务配置字段或版本无效')
    control=NativeControl(app_uid=value['app_uid'],team_id=value['team_id'],base_url=value['base_url'])
    try:pwd.getpwuid(control.app_uid)
    except KeyError:raise ValueError('服务配置的账户不存在') from None
    return control


def _parent_fd():
    for path in [SOCKET_PATH.parent,*SOCKET_PATH.parent.parents]:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('服务监听目录不受管理员权限保护')
    fd=os.open(SOCKET_PATH.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    info=os.fstat(fd)
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o755:
        os.close(fd);raise PermissionError('服务监听根目录必须为管理员所有的 0755 目录')
    return fd


class NativeListener:
    def __init__(self,control):
        native_ai_keys._root_required()
        self.control=control;self.parent=None;self.lease=None;self.socket=None;self.identity=None;self.closed=False
        try:
            self.parent=_parent_fd()
            try:
                self.lease=os.open('control.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.parent)
                os.fchmod(self.lease,0o600)
            except FileExistsError:
                self.lease=os.open('control.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self.parent)
            info=os.fstat(self.lease)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1:
                raise PermissionError('服务监听锁文件无效')
            try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('已有原生服务监听器') from None
            self._remove_stale()
            self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.socket.bind(str(SOCKET_PATH))
            info=os.stat(SOCKET_PATH.name,dir_fd=self.parent,follow_symlinks=False)
            self.identity=(info.st_dev,info.st_ino)
            os.chown(SOCKET_PATH,self.control.app_uid,-1);os.chmod(SOCKET_PATH,0o600)
            self.socket.listen(4)
        except BaseException:
            self._release();raise

    def _remove_stale(self):
        try:info=os.stat(SOCKET_PATH.name,dir_fd=self.parent,follow_symlinks=False)
        except FileNotFoundError:return
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=self.control.app_uid or stat.S_IMODE(info.st_mode)!=0o600:
            raise PermissionError('已有控制端点的类型、账户或权限无效')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            try:probe.connect(str(SOCKET_PATH))
            except OSError as error:
                if error.errno not in {errno.ECONNREFUSED,errno.ENOENT}:raise
            else:raise ValueError('已有活动控制端点，不能替换')
        current=os.stat(SOCKET_PATH.name,dir_fd=self.parent,follow_symlinks=False)
        if (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):
            raise ValueError('检查期间控制端点发生变化')
        os.unlink(SOCKET_PATH.name,dir_fd=self.parent)

    def serve_once(self,timeout=.25):
        if self.closed:raise ValueError('原生监听器已关闭')
        if type(timeout) not in {int,float} or not math.isfinite(timeout) or not 0<=timeout<=1:
            raise ValueError('监听等待必须在 0–1 秒之间')
        self.control.tick()
        if not select.select([self.socket],[],[],timeout)[0]:return None
        connection,_=self.socket.accept()
        return ipc.handle_connection(connection,self.control)

    def _release(self):
        if self.socket is not None:self.socket.close();self.socket=None
        try:
            if self.parent is not None and self.identity is not None:
                try:info=os.stat(SOCKET_PATH.name,dir_fd=self.parent,follow_symlinks=False)
                except FileNotFoundError:pass
                else:
                    if stat.S_ISSOCK(info.st_mode) and (info.st_dev,info.st_ino)==self.identity:
                        os.unlink(SOCKET_PATH.name,dir_fd=self.parent)
        finally:
            if self.lease is not None:os.close(self.lease);self.lease=None
            if self.parent is not None:os.close(self.parent);self.parent=None
            self.closed=True

    def close(self):
        if self.closed:return
        result=self.control.shutdown()
        if result.get('ok') is not True:raise OSError('服务记录保存未完成，监听器仍保留供重试')
        self._release()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def run_service():
    """Installed-service entry point; packaging must protect this code and Python.

    Signal handlers only request shutdown. Collection and queue persistence stay
    on the service thread, outside the asynchronous signal handler.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('服务入口必须在主线程运行')
    control=load_control()
    stopping=threading.Event()
    previous={}
    listener=None
    try:
        for number in (signal.SIGTERM,signal.SIGINT):
            previous[number]=signal.getsignal(number)
            signal.signal(number,lambda *_:stopping.set())
        listener=NativeListener(control)
        while not stopping.is_set():
            listener.serve_once(timeout=.25)
    finally:
        try:
            if listener is not None:listener.close()
        finally:
            for number,handler in previous.items():signal.signal(number,handler)
