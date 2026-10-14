"""Decode untrusted model output without losing valid reported consumption."""
import json


class ModelOutputRejected(ValueError):
    def __init__(self, input_tokens, output_tokens, runtime_ms):
        super().__init__('model output invalid; valid usage is available')
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.runtime_ms = runtime_ms


def decode_response(raw, kind, runtime_ms):
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError('model usage unavailable')
    if kind == 'ollama':
        incoming, outgoing = envelope.get('prompt_eval_count'), envelope.get('eval_count')
    else:
        usage = envelope.get('usage')
        incoming, outgoing = (usage.get('prompt_tokens'), usage.get('completion_tokens')) if isinstance(usage, dict) else (None, None)
    if any(type(value) is not int or value < 0 or value > 2**63-1 for value in (incoming, outgoing)):
        raise ValueError('model usage invalid or unavailable')
    invalid, output = False, None
    try:
        content = (envelope['message']['content'] if kind == 'ollama'
                   else envelope['choices'][0]['message']['content'])
        if not isinstance(content, str):
            invalid = True
        else:
            def invalid_constant(_):
                raise ValueError('nonfinite model output')
            output = json.loads(content, parse_constant=invalid_constant)
            invalid = not isinstance(output, dict)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        invalid = True
    # Raise outside the parser's except block: no raw response in exception context.
    if invalid:
        raise ModelOutputRejected(incoming, outgoing, runtime_ms)
    return output, incoming, outgoing


def settle_rejected_output(task, call, decision, provider, error, runtime_ms, namespace):
    import v5_runtime as r
    return r.record_usage(r.UsageReport(
        decision_id=decision['id'], call_id=call['id'],
        idempotency_key=f"{namespace}:{task['id']}:{task['attempt']}",
        input_tokens=error.input_tokens, output_tokens=error.output_tokens,
        runtime_ms=max(error.runtime_ms, runtime_ms),
        cost_micros=r._estimated_cost(provider, error.input_tokens + error.output_tokens)
            if decision['route'] == 'cloud' else 0))
