"""Input allowances and generation limits; estimates never claim exact token counts."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException


def message_hash(provider, messages):
    return hashlib.sha256(json.dumps({'model': provider['model'], 'messages': messages,
        'response_format': {'type': 'json_object'}}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def input_plan(provider, messages, total_tokens, counted=None):
    metadata = json.loads(provider['metadata_json']) if 'metadata_json' in provider.keys() else {}
    strategy = metadata.get('input_token_counting', 'utf8_estimate')
    if strategy == 'llama_cpp_server':
        if counted is None or type(counted) is not int or counted < 1:
            raise ValueError('model input counting requires a valid server count')
        incoming = counted
    elif strategy == 'utf8_estimate':
        # Text-only budgeting heuristic. Hidden server/template tokens may differ;
        # the immutable usage report remains authoritative after the request.
        incoming = len(json.dumps(messages, ensure_ascii=False).encode()) + 256
    else:
        raise ValueError('model input counting strategy unavailable')
    output = min(4000, total_tokens - incoming, metadata.get('max_context_tokens', 32768) - incoming)
    if output < 1:
        raise HTTPException(409, 'model input token budget exhausted before generation')
    return {'strategy': strategy, 'input_tokens': incoming, 'output_max_tokens': output,
            'request_sha256': message_hash(provider, messages)}


def prepare_call(provider, task, nodes, call):
    import v5_workers as w
    import v5_runtime as r
    from v5_runtime_calls import start_call
    from v5_model_transport import request_model
    metadata = r._load(provider['metadata_json'], {})
    messages = w._model_messages(task, nodes)
    counted = None
    if metadata.get('input_token_counting') == 'llama_cpp_server':
        if r._location(provider) != 'local' or provider['kind'] != 'llama_cpp':
            raise HTTPException(409, 'server input counting requires local llama.cpp')
        # This reservation now owns all request time, including server counting.
        start_call(call['id'])
        base = provider['base_url'].rstrip('/')
        prefix = base if base.endswith('/v1') else base + '/v1'
        timeout = int((datetime.fromisoformat(call['deadline_at']) - datetime.now(timezone.utc)).total_seconds() * 1000)
        raw = request_model(prefix + '/chat/completions/input_tokens',
            {'model': provider['model'], 'messages': messages, 'response_format': {'type': 'json_object'}},
            min(45000, max(1, timeout)), lambda: w._check_model_authority(provider, task))
        value = json.loads(raw)
        counted = value.get('input_tokens') if isinstance(value, dict) else None
    plan = input_plan(provider, messages, call['reserved_tokens'], counted)
    with w._core().connect() as db:
        db.execute('BEGIN IMMEDIATE')
        w._check_lease_attempt(w._owned_running(db, task['id'], task['lease_owner']), task['attempt'])
        db.execute('INSERT INTO runtime_call_inputs VALUES(?,?,?,?,?,?)', (
            call['id'], plan['strategy'], plan['input_tokens'], plan['output_max_tokens'], plan['request_sha256'], w._now()))
    if counted is None:
        start_call(call['id'])
    remaining = int((datetime.fromisoformat(call['deadline_at']) - datetime.now(timezone.utc)).total_seconds() * 1000)
    if remaining < 1:
        raise HTTPException(409, 'model call runtime budget exhausted during preflight')
    execution = dict(task)
    execution['budget_json'] = w._dump(w._load(task['budget_json'], {}) | {
        'max_tokens': call['reserved_tokens'], 'max_runtime_ms': remaining})
    execution['model_preflight'] = plan
    return execution
