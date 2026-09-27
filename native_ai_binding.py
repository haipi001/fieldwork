"""Bind live native imports to the public key of a protected installation."""
import base64
import binascii
import hmac
import json
import os
import platform
from pathlib import Path

from native_ai_launcher import protected_file,verify_installed_collector,NativeLaunchError,INSTALL_PATH

REGISTRY=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-audit/collector-public.json')


def authorized(collector):
    try:
        before=protected_file(REGISTRY)
        fd=os.open(REGISTRY,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=before:return False
            raw=source.read(4097)
        if len(raw)>4096:return False
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={'schema','team_id','public_key'} or value['schema']!='fieldwork-native-binding/1':return False
        public=base64.b64decode(value['public_key'],validate=True)
        supplied=base64.b64decode(collector['public_key'],validate=True)
        if len(public)!=32 or not hmac.compare_digest(public,supplied):return False
        verify_installed_collector(value['team_id'])
        return protected_file(REGISTRY)==before
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
        before=protected_file(REGISTRY)
        fd=os.open(REGISTRY,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=before:
                return {**result,'status':'installation_check_failed'}
            raw=source.read(4097)
        if len(raw)>4096:return {**result,'status':'installation_check_failed'}
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)!={'schema','team_id','public_key'}:
            return {**result,'status':'installation_check_failed'}
        if value['schema']!='fieldwork-native-binding/1' or len(base64.b64decode(value['public_key'],validate=True))!=32:
            return {**result,'status':'installation_check_failed'}
        verify_installed_collector(value['team_id'])
        if protected_file(REGISTRY)!=before:return {**result,'status':'installation_check_failed'}
        return {**result,'status':'installed_not_connected'}
    except (OSError,ValueError,TypeError,KeyError,RecursionError,binascii.Error,NativeLaunchError):
        return {**result,'status':'installation_check_failed'}
