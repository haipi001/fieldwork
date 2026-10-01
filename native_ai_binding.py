"""Bind live native imports to the public key of a protected installation."""
import base64
import binascii
import hmac
import json
import os
import platform
import re
from pathlib import Path

from native_ai_launcher import protected_file,verify_installed_collector,NativeLaunchError,INSTALL_PATH

REGISTRY=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/collector-public.json')


def read_registration():
    """Verified public installation identity for local pairing; no private keys."""
    before=protected_file(REGISTRY)
    fd=os.open(REGISTRY,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=before:
            raise ValueError('读取期间原生登记发生变化')
        raw=source.read(4097)
    if len(raw)>4096:raise ValueError('原生登记超过上限')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('原生登记包含重复字段')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=pairs)
    if (not isinstance(value,dict) or set(value)!={'schema','team_id','public_key'}
            or value['schema']!='fieldwork-native-binding/1'
            or not isinstance(value['team_id'],str) or not re.fullmatch(r'[A-Z0-9]{10}',value['team_id'])
            or not isinstance(value['public_key'],str)):
        raise ValueError('原生登记字段无效')
    public=base64.b64decode(value['public_key'],validate=True)
    if len(public)!=32 or base64.b64encode(public).decode()!=value['public_key']:
        raise ValueError('原生登记公钥无效')
    verify_installed_collector(value['team_id'])
    if protected_file(REGISTRY)!=before:raise ValueError('验证期间原生登记发生变化')
    return value


def authorized(collector):
    try:
        value=read_registration()
        public=base64.b64decode(value['public_key'],validate=True)
        supplied=base64.b64decode(collector['public_key'],validate=True)
        if len(public)!=32 or not hmac.compare_digest(public,supplied):return False
        return True
    except (OSError,ValueError,TypeError,KeyError,RecursionError,binascii.Error,NativeLaunchError):
        return False


def installation_status():
    """Read-only prerequisite check; never asserts live collection or permission."""
    result={'status':'not_installed','live_verified':False}
    if platform.system()!='Darwin':return {**result,'status':'unsupported_platform'}
    try:INSTALL_PATH.lstat()
    except FileNotFoundError:return result
    except OSError:return {**result,'status':'installation_check_failed'}
    try:
        read_registration()
        return {**result,'status':'installed_not_connected'}
    except (OSError,ValueError,TypeError,KeyError,RecursionError,binascii.Error,NativeLaunchError):
        return {**result,'status':'installation_check_failed'}
