"""Bind live native imports to the public key of a protected installation."""
import base64
import binascii
import hmac
import json
import os
from pathlib import Path

from native_ai_launcher import protected_file,verify_installed_collector,NativeLaunchError

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
