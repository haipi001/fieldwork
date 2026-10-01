"""Tail local Codex and Claude Code tool metadata, never persist message bodies.

Application logs are application-provided evidence, not independent OS telemetry.
The first scan establishes an EOF baseline; historical conversations are not imported.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

MAX_FILES = 4096
MAX_BYTES = 4 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_RECORDS = 2000


def roots():
    return {'Codex': Path.home() / '.codex/sessions',
            'Claude Code': Path.home() / '.claude/projects'}


def fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def discover(locations=None):
    files, sources, errors = [], [], []
    for app, root in (locations or roots()).items():
        found = []
        try:
            if root.is_dir():
                # Do not follow symlinked logs outside the known application directory.
                for path in root.rglob('*.jsonl'):
                    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                        continue
                    stat = path.stat()
                    if path.is_file():
                        found.append((app, path, stat))
            sources.append({'app': app, 'status': 'available' if found else 'waiting', 'log_count': len(found)})
            files.extend(found)
        except OSError:
            errors.append(f'{app} 工具日志不可读取，将自动重试')
            sources.append({'app': app, 'status': 'unavailable', 'log_count': len(found)})
    files.sort(key=lambda item: item[2].st_mtime_ns, reverse=True)
    if len(files) > MAX_FILES:
        errors.append(f'工具日志仅跟踪最近 {MAX_FILES} 个文件，其他会话存在覆盖缺口')
    return files[:MAX_FILES], sources, errors


def summary():
    _, sources, errors = discover()
    return {'sources': sources, 'errors': errors, 'evidence_type': 'application_log'}


def valid_time(value, since):
    try:
        timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        start = datetime.fromisoformat(since.replace('Z', '+00:00'))
        return timestamp.tzinfo is not None and timestamp >= start
    except (ValueError, TypeError, AttributeError):
        return False


def records(raw, app):
    """Accept only supported record shapes actually observed locally."""
    if app == 'Codex' and raw.get('type') == 'response_item':
        item = raw.get('payload')
        if not isinstance(item, dict):
            return []
        kind = item.get('type')
        if kind in {'function_call', 'custom_tool_call'}:
            return [('request', item.get('call_id'), item.get('name'), False)]
        if kind in {'function_call_output', 'custom_tool_call_output'}:
            return [('result', item.get('call_id'), None, False)]
    if app == 'Claude Code' and raw.get('type') in {'assistant', 'user'}:
        message = raw.get('message')
        content = message.get('content', []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            return []
        parsed = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'tool_use':
                parsed.append(('request', item.get('id'), item.get('name'), False))
            elif item.get('type') == 'tool_result':
                parsed.append(('result', item.get('tool_use_id'), None, item.get('is_error') is True))
        return parsed
    return []


def extract(raw, app, file_key, pending, session, since):
    if not isinstance(raw, dict) or not valid_time(raw.get('timestamp'), since):
        return []
    output = []
    for kind, call_id, name, error in records(raw, app):
        if not isinstance(call_id, str) or not call_id or len(call_id) > 512:
            continue
        call = fingerprint(file_key + '|' + call_id)
        if kind == 'request':
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:/-]{0,127}', name):
                continue
            source = 'mcp' if name.startswith('mcp__') else 'tool_call'
            pending[call] = {'tool_name': name, 'source_type': source}
        tool = pending.get(call, {'tool_name': 'unknown', 'source_type': 'tool_call'})
        phase = '工具请求' if kind == 'request' else '工具错误结果' if error else '工具结果'
        output.append({'timestamp': raw['timestamp'], 'actor': app, 'session_id': session,
            'source_type': tool['source_type'], 'action_type': 'invoke_tool',
            'tool_name': tool['tool_name'], 'tool_call_ref': call,
            'application_session_ref': file_key, 'attribution_method': 'application_log',
            'mcp_server': tool['tool_name'].split('__')[1] if tool['source_type'] == 'mcp' else '',
            'status': 'requested' if kind == 'request' else 'result_error' if error else 'result_recorded',
            'command_category': 'application_tool_request' if kind == 'request' else 'application_tool_result',
            'resource': f"应用日志记录{phase}：{app} · {tool['tool_name']}"})
        if kind == 'result':
            pending.pop(call, None)
    return output


def anchor(stream, offset):
    stream.seek(max(0, offset - 64))
    return hashlib.sha256(stream.read(min(64, offset))).hexdigest()


def scan(previous, now, session, locations=None, reset=False):
    files, sources, errors = discover(locations)
    initial = not previous or reset
    state = {'version': 1, 'since': now if initial else previous['since'],
             'files': {} if initial else dict(previous.get('files', {})),
             'pending': {} if initial else dict(previous.get('pending', {})),
             'next_file': None}
    if not initial and previous.get('next_file'):
        keys = [fingerprint(app + '|' + str(path)) for app, path, _ in files]
        if previous['next_file'] in keys:
            first = keys.index(previous['next_file'])
            files = files[first:] + files[:first]
    observations, total, record_count = [], 0, 0
    for index, (app, path, stat) in enumerate(files):
        key = fingerprint(app + '|' + str(path))
        old = state['files'].get(key)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                stat = os.fstat(stream.fileno())
                if initial:
                    offset = stat.st_size
                    stream.seek(max(0, offset - 1))
                    skip = bool(offset and stream.read(1) != b'\n')
                else:
                    rotated = not old or old['inode'] != stat.st_ino or old['device'] != stat.st_dev or old['offset'] > stat.st_size
                    if not rotated and anchor(stream, old['offset']) != old.get('anchor'):
                        rotated = True
                    offset, skip = (0, False) if rotated else (old['offset'], old.get('skip', False))
                    stream.seek(offset)
                    while total < MAX_BYTES and record_count < MAX_RECORDS:
                        position = stream.tell()
                        line = stream.readline(min(MAX_LINE + 1, MAX_BYTES - total))
                        if not line:
                            break
                        total += len(line)
                        complete = line.endswith(b'\n')
                        if skip or len(line) > MAX_LINE:
                            if len(line) > MAX_LINE:
                                errors.append(f'{app} 日志行超过大小上限，已跳过')
                            skip = not complete
                            offset = stream.tell()
                            continue
                        if not complete:
                            # An append in progress is retried, not discarded.
                            offset = position
                            break
                        offset = stream.tell()
                        record_count += 1
                        try:
                            raw = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            errors.append(f'{app} 存在无法解析的日志行，已跳过')
                            continue
                        observations.extend(extract(raw, app, key, state['pending'], session, state['since']))
                state['files'][key] = {'inode': stat.st_ino, 'device': stat.st_dev, 'offset': offset,
                                       'skip': skip, 'anchor': anchor(stream, offset)}
        except OSError:
            errors.append(f'{app} 日志读取失败，将从上次位置重试')
        if total >= MAX_BYTES or record_count >= MAX_RECORDS:
            next_app, next_path, _ = files[(index + 1) % len(files)]
            state['next_file'] = fingerprint(next_app + '|' + str(next_path))
            break
    if total >= MAX_BYTES or record_count >= MAX_RECORDS:
        errors.append('工具日志采集存在积压，将在下一轮继续读取')
    if len(state['pending']) > MAX_RECORDS:
        state['pending'] = dict(list(state['pending'].items())[-MAX_RECORDS:])
        errors.append('工具请求关联超过上限，部分结果可能缺少工具名称')
    return observations, state, {'sources': sources, 'errors': sorted(set(errors)),
                                 'evidence_type': 'application_log'}
