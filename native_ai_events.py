"""Validate metadata from the native ES producer before signing or importing."""
from datetime import datetime

KINDS = {'native_exec': ('process', 'execute'), 'native_fork': ('process', 'spawn'),
         'native_exit': ('process', 'unknown'), 'native_open': ('filesystem', 'unknown'),
         'native_close': ('filesystem', 'unknown'), 'native_write': ('filesystem', 'write'),
         'native_unlink': ('filesystem', 'modify'), 'native_rename': ('filesystem', 'modify')}
FIELDS = {'schema', 'timestamp', 'actor', 'source_type', 'action_type', 'status', 'command_category',
    'process_id', 'parent_process_id', 'process_pid_version', 'process_started_at', 'process_executable',
    'attribution_method', 'filesystem_path', 'resource', 'destination', 'modified', 'mapped_writable',
    'native_sequence', 'collector_dropped', 'kernel_dropped', 'synthetic', 'native_authorization',
    'path_truncated', 'destination_truncated', 'executable_truncated'}


def normalize(record, session_id):
    if not isinstance(record, dict) or set(record) - FIELDS:
        raise ValueError('原生采集元数据包含未知字段')
    if record.get('schema') != 'fieldwork-native-es/1' or record.get('synthetic') is not False:
        raise ValueError('不接受模拟或未知结构作为真实系统事件')
    kind = record.get('command_category')
    if kind not in KINDS or (record.get('source_type'), record.get('action_type')) != KINDS[kind]:
        raise ValueError('系统事件类型与动作不一致')
    if record.get('status') not in {'observed', 'blocked'}:
        raise ValueError('系统通知不能直接声明操作副作用成功')
    if record.get('native_authorization') not in {'allowed','denied','flags'}:
        raise ValueError('缺少原生授权结果元数据')
    if (record['status'] == 'blocked') != (record['native_authorization'] == 'denied'):
        raise ValueError('原生授权结果与状态不一致')
    time = datetime.fromisoformat(str(record.get('timestamp', '')).replace('Z', '+00:00'))
    if time.tzinfo is None:
        raise ValueError('系统事件时间缺少时区')
    for key in ('process_id', 'parent_process_id', 'process_pid_version', 'collector_dropped', 'kernel_dropped', 'native_sequence'):
        number = record.get(key)
        if key == 'native_sequence' and number is None:
            continue
        if type(number) is not int or not 0 <= number <= 9223372036854775807:
            raise ValueError('系统事件进程/序列字段无效')
    for key in ('modified', 'mapped_writable', 'path_truncated', 'destination_truncated', 'executable_truncated'):
        if type(record.get(key)) is not bool:
            raise ValueError('文件状态标志必须是布尔值')
    for key in ('actor','process_executable','process_started_at','attribution_method','filesystem_path','resource','destination'):
        value = record.get(key)
        if not isinstance(value, str) or len(value) > 5000 or '\x00' in value:
            raise ValueError('系统事件文本字段无效')
    event = dict(record)
    event['session_id'] = session_id
    return event
