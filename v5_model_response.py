"""Decode untrusted model output without losing valid reported consumption."""
import json

REJECTION_REASONS = frozenset({"invalid_json", "output_truncated", "content_filtered", "non_text_content",
    "non_object_json", "invalid_json_constant", "json_too_deep", "missing_output"})
FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "function_call", "load", "unload")


class ModelOutputRejected(ValueError):
    def __init__(self, input_tokens, output_tokens, runtime_ms, reason_code="invalid_json", finish_reason=None):
        super().__init__('model output invalid; valid usage is available')
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.runtime_ms = runtime_ms
        self.reason_code = reason_code if isinstance(reason_code, str) and reason_code in REJECTION_REASONS else "invalid_json"
        self.finish_reason = finish_reason if isinstance(finish_reason, str) and finish_reason in FINISH_REASONS else None


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
    reason, output, finish = None, None, None
    try:
        choice = envelope['message'] if kind == 'ollama' else envelope['choices'][0]
        reported_finish = envelope.get('done_reason') if kind == 'ollama' else choice.get('finish_reason')
        finish = reported_finish if reported_finish in FINISH_REASONS else None
        content = choice['content'] if kind == 'ollama' else choice['message']['content']
        if finish == 'length':
            reason = 'output_truncated'
        elif finish == 'content_filter':
            reason = 'content_filtered'
        elif not isinstance(content, str):
            reason = 'non_text_content'
        else:
            def invalid_constant(_):
                raise ValueError('nonfinite model output')
            output = json.loads(content, parse_constant=invalid_constant)
            if not isinstance(output, dict):
                reason = 'non_object_json'
    except json.JSONDecodeError:
        reason = 'invalid_json'
    except RecursionError:
        reason = 'json_too_deep'
    except ValueError:
        reason = 'invalid_json_constant'
    except (TypeError, KeyError, IndexError, AttributeError):
        reason = 'missing_output'
    # Raise outside the parser's except block: never retain content or parser context.
    if reason:
        raise ModelOutputRejected(incoming, outgoing, runtime_ms, reason, finish)
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
