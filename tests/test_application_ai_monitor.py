import json

import application_ai_monitor as monitor

NOW = '2026-09-26T12:00:00+00:00'
LATER = '2026-09-26T12:00:01+00:00'


def write(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')


def request(timestamp= LATER, call='call-1'):
    return {'timestamp': timestamp, 'type': 'response_item', 'payload': {
        'type': 'function_call', 'name': 'mcp__local__read', 'call_id': call,
        'arguments': '{"token":"super-secret","prompt":"private-message"}'}}


def test_baseline_append_restart_and_redacted_metadata(tmp_path):
    path = tmp_path / 'session.jsonl'
    write(path, request())
    locations = {'Codex': tmp_path}
    events, state, summary = monitor.scan({}, NOW, 'audit-session', locations)
    assert not events  # No historical import, even with a future-looking log timestamp.
    assert summary['sources'][0]['status'] == 'available'
    write(path, request(call='call-2'))
    write(path, {'timestamp': LATER, 'type': 'response_item', 'payload': {
        'type': 'function_call_output', 'call_id': 'call-2', 'output': 'secret-result'}})
    events, state, _ = monitor.scan(state, LATER, 'audit-session', locations)
    assert [e['status'] for e in events] == ['requested', 'result_recorded']
    assert all(e['tool_name'] == 'mcp__local__read' for e in events)
    assert all(e['mcp_server'] == 'local' for e in events)
    assert len({e['tool_call_ref'] for e in events}) == 1
    assert not any(secret in json.dumps((events, state)) for secret in ('super-secret', 'private-message', 'secret-result'))
    restored = json.loads(json.dumps(state))
    assert monitor.scan(restored, LATER, 'audit-session', locations)[0] == []


def test_partial_append_and_claude_result_error(tmp_path):
    path = tmp_path / 'session.jsonl'
    path.touch()
    locations = {'Claude Code': tmp_path}
    _, state, _ = monitor.scan({}, NOW, 's', locations)
    value = {'timestamp': LATER, 'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'private chat'},
        {'type': 'tool_use', 'id': 'tool-1', 'name': 'Bash', 'input': {'command': 'private command'}}]}}
    line = json.dumps(value)
    path.write_text(line[:20])
    assert monitor.scan(state, LATER, 's', locations)[0] == []
    with path.open('a') as stream:
        stream.write(line[20:] + '\n')
    events, state, _ = monitor.scan(state, LATER, 's', locations)
    assert len(events) == 1 and events[0]['tool_name'] == 'Bash'
    write(path, {'timestamp': LATER, 'type': 'user', 'message': {'content': [
        {'type': 'tool_result', 'tool_use_id': 'tool-1', 'is_error': True, 'content': 'private error'}]}})
    events, state, _ = monitor.scan(state, LATER, 's', locations)
    assert events[0]['status'] == 'result_error'
    assert events[0]['tool_name'] == 'Bash'
    assert 'private' not in json.dumps((events, state))


def test_rotation_old_timestamps_and_reset_do_not_replay(tmp_path):
    path = tmp_path / 'session.jsonl'
    path.touch()
    locations = {'Codex': tmp_path}
    _, state, _ = monitor.scan({}, NOW, 's', locations)
    write(path, request())
    _, state, _ = monitor.scan(state, LATER, 's', locations)
    path.write_text('')
    write(path, request(timestamp='2020-01-01T00:00:00+00:00'))
    write(path, request(call='fresh'))
    events, state, _ = monitor.scan(state, LATER, 's', locations)
    assert len(events) == 1
    write(path, request(call='during-pause'))
    events, state, _ = monitor.scan(state, LATER, 's', locations, reset=True)
    assert not events
    assert monitor.scan(state, LATER, 's', locations)[0] == []


def test_symlink_and_malformed_records_are_not_trusted(tmp_path):
    root = tmp_path / 'logs'
    root.mkdir()
    outside = tmp_path / 'outside.jsonl'
    write(outside, request())
    (root / 'linked.jsonl').symlink_to(outside)
    files, _, _ = monitor.discover({'Codex': root})
    assert not files
    assert monitor.extract({'timestamp': LATER, 'type': 'response_item', 'payload': {
        'type': 'function_call', 'call_id': 'x', 'name': '<script>alert(1)</script>'}}, 'Codex', 'file', {}, 's', NOW) == []


def test_busy_log_does_not_starve_other_sessions(tmp_path, monkeypatch):
    a, b = tmp_path / 'a.jsonl', tmp_path / 'b.jsonl'
    a.touch()
    b.touch()
    locations = {'Codex': tmp_path}
    _, state, _ = monitor.scan({}, NOW, 's', locations)
    monkeypatch.setattr(monitor, 'MAX_RECORDS', 1)
    write(a, request(call='a-1'))
    write(b, request(call='b-1'))
    write(b, request(call='b-2'))
    first, state, _ = monitor.scan(state, LATER, 's', locations)
    write(b, request(call='b-3'))
    second, state, _ = monitor.scan(state, LATER, 's', locations)
    assert len(first) == len(second) == 1
    assert first[0]['application_session_ref'] != second[0]['application_session_ref']
