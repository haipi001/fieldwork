"""Passive macOS AI process and TCP sampling. Never reads argv or file contents.

Snapshots are observations, not a complete system audit stream. Short-lived
processes/connections and browser web apps require additional collectors.
"""
from __future__ import annotations

import platform
import plistlib
import re
import subprocess
from pathlib import Path

APPS = {'codex': 'Codex', 'chatgpt': 'ChatGPT', 'claude': 'Claude',
        'cursor': 'Cursor', 'windsurf': 'Windsurf', 'ollama': 'Ollama',
        'lm studio': 'LM Studio', 'lmstudio': 'LM Studio',
        'jan': 'Jan', 'cherry studio': 'Cherry Studio',
        'cherrystudio': 'Cherry Studio', 'trae': 'Trae',
        'copilot': 'Copilot', 'aider': 'Aider', 'continue': 'Continue'}


def identify(executable):
    # Match executable names or .app components, never arbitrary substrings.
    pieces = [Path(executable).name.lower()]
    pieces += [part[:-4].lower() for part in Path(executable).parts if part.lower().endswith('.app')]
    for piece in pieces:
        for key, name in APPS.items():
            if piece == key or piece.startswith(key + ' helper'):
                return name
    return None


def parse_processes(output):
    processes = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4 or not all(field.isdigit() for field in fields[:3]):
            continue
        pid, parent, uid = map(int, fields[:3])
        executable = fields[3]
        # Process executable paths only; ps comm does not expose argument data.
        app = identify(executable)
        processes[pid] = {'pid': pid, 'parent_pid': parent, 'uid': uid,
                          'executable': Path(executable).name, 'app': app}
    changed = True
    while changed:
        changed = False
        for process in processes.values():
            parent = processes.get(process['parent_pid'])
            if not process['app'] and parent and parent['app']:
                process['app'] = parent['app']
                changed = True
    return {str(pid): process for pid, process in processes.items() if process['app']}


def parse_connections(output, processes):
    connections, pid, endpoint = {}, None, None
    for line in output.splitlines():
        if line.startswith('p'):
            pid, endpoint = line[1:], None
        elif line.startswith('f'):
            endpoint = None
        elif line.startswith('n'):
            endpoint = line[1:]
        elif line == 'TST=ESTABLISHED' and pid in processes and endpoint and '->' in endpoint:
            remote = endpoint.split('->', 1)[1]
            match = re.fullmatch(r'(\[[^\]]+\]|[^:]+):(\d+)', remote)
            if match:
                host, port = match[1].strip('[]'), int(match[2])
                key = f'{pid}|{endpoint}'
                connections[key] = {'pid': int(pid), 'app': processes[pid]['app'],
                                    'host': host, 'port': port}
    return connections


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    return result


def parse_open_files(output, processes):
    """Observe regular file descriptors; access mode is not proof of an I/O operation."""
    files, pid, descriptor, access, kind = {}, None, None, None, None
    for line in output.splitlines():
        if line.startswith('p'):
            pid, descriptor, access, kind = line[1:], None, None, None
        elif line.startswith('f'):
            descriptor, access, kind = line[1:], None, None
        elif line.startswith('a'):
            access = line[1:]
        elif line.startswith('t'):
            kind = line[1:]
        elif line.startswith('n') and pid in processes and kind == 'REG' and descriptor and descriptor.isdigit() and line[1:].startswith('/'):
            path = line[1:]
            files[f'{pid}|{descriptor}|{path}'] = {
                'pid': int(pid), 'app': processes[pid]['app'],
                'path': path, 'access': access or 'unknown'}
    return files


def installed_apps():
    found = {}
    for root in (Path('/Applications'), Path.home() / 'Applications'):
        if not root.is_dir():
            continue
        for app_path in root.glob('*.app'):
            name = identify(str(app_path / app_path.stem))
            if not name:
                continue
            try:
                with (app_path / 'Contents/Info.plist').open('rb') as source:
                    info = plistlib.load(source)
                found[name] = {'name': name, 'bundle_id': info.get('CFBundleIdentifier', ''), 'installed': True}
            except (OSError, ValueError, plistlib.InvalidFileException):
                found[name] = {'name': name, 'installed': True, 'bundle_id': ''}
    return list(found.values())


def snapshot():
    result = {'platform': platform.system(), 'processes': {}, 'connections': {}, 'open_files': {},
              'applications': [], 'errors': [], 'coverage': {
                  'processes': 'unavailable', 'network': 'unavailable',
                  'file_io': 'not_connected', 'open_files': 'unavailable', 'mcp': 'not_connected',
                  'browser_ai': 'not_connected', 'prompt_content': 'not_collected'}}
    if platform.system() != 'Darwin':
        result['errors'].append('本机采集器目前支持 macOS')
        return result
    result['applications'] = installed_apps()
    try:
        ps = run(['/bin/ps', '-axo', 'pid=,ppid=,uid=,comm='])
        if ps.returncode:
            result['errors'].append('进程读取失败')
            return result
        result['processes'] = parse_processes(ps.stdout)
        result['coverage']['processes'] = 'sampling'
        if not result['processes']:
            result['coverage']['network'] = 'sampling'
            result['coverage']['open_files'] = 'sampling'
            return result
        sockets = run(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(result['processes']), '-iTCP', '-FpnT'])
        if sockets.returncode not in (0, 1) or sockets.stderr.strip():
            result['errors'].append('部分网络连接不可见；仅记录当前用户可读取的连接')
            result['coverage']['network'] = 'partial'
        else:
            result['coverage']['network'] = 'sampling'
        result['connections'] = parse_connections(sockets.stdout, result['processes'])
        files = run(['/usr/sbin/lsof', '-nP', '-a', '-p', ','.join(result['processes']), '-Fpfatn'])
        result['coverage']['open_files'] = 'partial' if files.returncode not in (0, 1) or files.stderr.strip() else 'sampling'
        if result['coverage']['open_files'] == 'partial':
            result['errors'].append('部分打开文件不可见；仅记录当前用户可读取的描述符')
        result['open_files'] = parse_open_files(files.stdout, result['processes'])
    except (OSError, subprocess.TimeoutExpired):
        result['errors'].append('本机采集暂时不可用，将在下一轮重试')
    return result


def events(previous, current, now, session):
    observations = []
    for pid, process in current['processes'].items():
        if previous.get('processes', {}).get(pid) == process:
            continue
        observations.append({'timestamp': now, 'actor': process['app'], 'session_id': session,
            'source_type': 'process', 'action_type': 'unknown', 'status': 'observed',
            'resource': f"发现运行进程：{process['app']} · {process['executable']} · PID {pid}",
            'command_category': 'process_observed'})
    for pid, process in previous.get('processes', {}).items():
        if current['coverage']['processes'] == 'sampling' and pid not in current['processes']:
            observations.append({'timestamp': now, 'actor': process['app'], 'session_id': session,
                'source_type': 'process', 'action_type': 'unknown', 'status': 'observed',
                'resource': f"进程已不在快照中：{process['app']} · PID {pid}",
                'command_category': 'process_disappeared'})
    for key, connection in current['connections'].items():
        if key in previous.get('connections', {}):
            continue
        observations.append({'timestamp': now, 'actor': connection['app'], 'session_id': session,
            'source_type': 'network', 'action_type': 'connect', 'status': 'connected',
            'network_host': connection['host'], 'network_port': connection['port'],
            'resource': f"{connection['app']} · PID {connection['pid']} · TCP {connection['host']}:{connection['port']}",
            'command_category': 'connection_observed'})
    for key, item in current.get('open_files', {}).items():
        if previous.get('open_files', {}).get(key) == item:
            continue
        access = {'r': '可读', 'w': '可写', 'u': '可读写'}.get(item['access'], '访问模式未知')
        observations.append({'timestamp': now, 'actor': item['app'], 'session_id': session,
            'source_type': 'filesystem', 'action_type': 'unknown', 'status': 'observed',
            'filesystem_path': item['path'],
            'resource': f"观察到打开文件：{item['app']} · PID {item['pid']} · {access} · {item['path']}",
            'command_category': 'open_file_observed'})
    return observations
