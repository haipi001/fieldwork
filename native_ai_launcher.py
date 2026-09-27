"""Fail-closed launch gate; installing/signing and system consent are external."""
import os
import platform
import plistlib
import re
import stat
import subprocess
from xml.parsers.expat import ExpatError
from pathlib import Path

INSTALL_PATH=Path('/Library/PrivilegedHelperTools/com.fieldwork.native-ai-collector')
IDENTIFIER='com.fieldwork.native-ai-collector'


class NativeLaunchError(ValueError):
    pass


def protected_file(path,*,executable=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise NativeLaunchError('采集器路径必须为绝对安装路径')
    for item in [path,*path.parents]:
        try:info=item.lstat()
        except FileNotFoundError:raise NativeLaunchError('原生采集器尚未安装') from None
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise NativeLaunchError('采集器或安装目录不受管理员权限保护')
        if item==path:
            if not stat.S_ISREG(info.st_mode) or executable and not info.st_mode&0o111:
                raise NativeLaunchError('受保护文件的类型或执行权限无效')
            identity=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        elif not stat.S_ISDIR(info.st_mode):
            raise NativeLaunchError('采集器父路径不是目录')
    return identity


def protected_binary(path):return protected_file(path,executable=True)


def verify_signature(path,team_id):
    if platform.system()!='Darwin':raise NativeLaunchError('原生采集器仅支持 macOS')
    if not isinstance(team_id,str) or not re.fullmatch(r'[A-Z0-9]{10}',team_id):
        raise NativeLaunchError('尚未配置可信签名团队')
    requirement=f'anchor apple generic and identifier "{IDENTIFIER}" and certificate leaf[subject.OU] = "{team_id}"'
    def run(arguments):
        try:
            result=subprocess.run(['/usr/bin/codesign',*arguments,str(path)],
                stdin=subprocess.DEVNULL,capture_output=True,timeout=10,check=False,
                env={'PATH':'/usr/bin:/bin','LANG':'C'})
        except (OSError,subprocess.TimeoutExpired):
            raise NativeLaunchError('代码签名检查不可用或超时') from None
        if result.returncode!=0:raise NativeLaunchError('代码签名或可信身份检查未通过')
        return result.stdout
    run(['--verify','--strict','-R='+requirement])
    raw=run(['--display','--entitlements',':-'])
    if len(raw)>65536:raise NativeLaunchError('签名权限声明超过上限')
    try:entitlements=plistlib.loads(raw)
    except (ValueError,plistlib.InvalidFileException,ExpatError):raise NativeLaunchError('无法读取签名权限声明') from None
    if not isinstance(entitlements,dict) or entitlements.get('com.apple.developer.endpoint-security.client') is not True:
        raise NativeLaunchError('签名缺少 Endpoint Security entitlement')
    if entitlements.get('com.apple.security.get-task-allow') is True:
        raise NativeLaunchError('生产采集器不能启用调试权限')
    return True


def verify_installed_collector(team_id):
    before=protected_binary(INSTALL_PATH)
    verify_signature(INSTALL_PATH,team_id)
    if protected_binary(INSTALL_PATH)!=before:
        raise NativeLaunchError('签名检查期间采集器发生变化')
    return before


def launch_installed_collector(team_id):
    if os.geteuid()!=0:raise NativeLaunchError('采集器需要已授权的管理员服务，不能直接从网页提升权限')
    identity=verify_installed_collector(team_id)
    if protected_binary(INSTALL_PATH)!=identity:raise NativeLaunchError('采集器安装状态发生变化')
    try:
        return subprocess.Popen([str(INSTALL_PATH)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,close_fds=True,start_new_session=True,env={'PATH':'/usr/bin:/bin','LANG':'C'})
    except OSError:
        raise NativeLaunchError('操作系统未能启动已验证的采集器') from None
