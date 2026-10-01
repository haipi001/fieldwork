"""Local stream protocol with OS peer credentials. Does not install a listener."""
import ctypes
import json
import math
import os
import platform
import re
import socket
import stat
import struct
import time
from functools import lru_cache
from pathlib import Path

from native_ai_control import MAX_REQUEST,PUBLIC_FIELDS,decode_request

SOCKET_PATH=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/control.sock')
MAX_RESPONSE=8192


@lru_cache(maxsize=1)
def _getpeereid():
    if platform.system()!='Darwin':raise PermissionError('本机原生控制目前仅支持 macOS')
    library=ctypes.CDLL(None,use_errno=True)
    function=library.getpeereid
    function.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
    function.restype=ctypes.c_int
    return function


def peer_identity(connection):
    if connection.family!=socket.AF_UNIX or connection.type&0xf!=socket.SOCK_STREAM:
        raise PermissionError('原生控制必须使用本机流式套接字')
    uid=ctypes.c_uint();gid=ctypes.c_uint()
    if _getpeereid()(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid))!=0:
        raise PermissionError('无法读取系统连接身份')
    return uid.value,gid.value


def _read_exact(connection,count,deadline):
    value=bytearray()
    while len(value)<count:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('本机控制连接超时')
        connection.settimeout(remaining)
        chunk=connection.recv(min(2048,count-len(value)))
        if not chunk:raise ValueError('本机控制帧不完整')
        value.extend(chunk)
    return bytes(value)


def _read_frame(connection,limit,deadline):
    length=struct.unpack('!I',_read_exact(connection,4,deadline))[0]
    if not 1<=length<=limit:raise ValueError('本机控制帧大小无效')
    return _read_exact(connection,length,deadline)


def _reply(connection,value,timeout):
    try:raw=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    except (TypeError,ValueError):raw=b'{"ok":false,"error_code":"invalid_response"}'
    if len(raw)>MAX_RESPONSE:raw=b'{"ok":false,"error_code":"response_too_large"}'
    connection.settimeout(timeout)
    connection.sendall(struct.pack('!I',len(raw))+raw)


def handle_connection(connection,control,*,timeout=2):
    """Owns one accepted connection; one bounded request, then close."""
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not .01<=timeout<=5:
        raise ValueError('本机控制读取等待必须在 0.01–5 秒之间')
    with connection:
        try:
            uid,_=peer_identity(connection)
            if uid!=control.app_uid:result={'ok':False,'error_code':'account_not_authorized'}
            else:
                raw=_read_frame(connection,MAX_REQUEST,time.monotonic()+timeout)
                result=control.handle(raw,peer_uid=uid)
        except PermissionError:result={'ok':False,'error_code':'peer_identity_unavailable'}
        except TimeoutError:result={'ok':False,'error_code':'control_read_timeout'}
        except (ValueError,OSError):result={'ok':False,'error_code':'invalid_control_frame'}
        except Exception:result={'ok':False,'error_code':'control_failed'}
        try:_reply(connection,result,timeout)
        except (TimeoutError,OSError):pass
        return result


def _validate_endpoint():
    for path in SOCKET_PATH.parents:
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise PermissionError('控制端点父目录不受管理员权限保护')
    info=SOCKET_PATH.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600:
        raise PermissionError('控制套接字的账户、权限或类型无效')
    return info.st_dev,info.st_ino,info.st_mtime_ns


def request(raw,*,timeout=75):
    """Client for the fixed protected socket; never transmits to a non-root peer."""
    decode_request(raw)
    if type(timeout) not in {int,float} or not math.isfinite(timeout) or not .01<=timeout<=75:
        raise ValueError('本机控制等待必须在 0.01–75 秒之间')
    before=_validate_endpoint()
    deadline=time.monotonic()+timeout
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout);connection.connect(str(SOCKET_PATH))
        if peer_identity(connection)[0]!=0:raise PermissionError('控制服务不是管理员进程')
        if _validate_endpoint()!=before:raise PermissionError('控制端点在连接期间发生变化')
        connection.sendall(struct.pack('!I',len(raw))+raw)
        response=_read_frame(connection,MAX_RESPONSE,deadline)
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('本机控制响应包含重复字段')
            value[key]=item
        return value
    value=json.loads(response,object_pairs_hook=pairs)
    if not isinstance(value,dict) or type(value.get('ok')) is not bool:
        raise ValueError('本机控制响应无效')
    if not set(value)<=PUBLIC_FIELDS|{'ok','idempotent','control_error_code'}:
        raise ValueError('本机控制响应字段无效')
    booleans={'ok','idempotent','producer_alive','pending_batch','forced_termination','stdout_drained','protocol_incomplete'}
    numbers={'events_read','buffered_records','unpersisted_buffer_bytes','unsaved_records'}
    for key,item in value.items():
        if key in booleans:valid=type(item) is bool
        elif key in numbers:valid=type(item) is int and 0<=item<2**63
        elif key=='exit_code':valid=item is None or type(item) is int and -255<=item<=255
        else:valid=item is None or isinstance(item,str) and re.fullmatch(r'[a-z_]{1,64}',item)
        if not valid:raise ValueError('本机控制响应类型无效')
    return value
