"""Fixed offline policy reconstruction entry. Receives data, never commands."""
import json
import os
import sys
import agent_audit as audit


def reconstruct(window):
    inputs = window['inputs']
    if audit.digest(inputs) != window['input_digest']:
        raise ValueError('input_digest_mismatch')
    events = inputs['events']
    if not isinstance(events, list) or not 1 <= len(events) <= 500:
        raise ValueError('invalid_window_size')
    evaluations = {}
    signals = []
    for event in events:
        policy_id = event.get('monitor_policy_id')
        policy = inputs['monitor_policies'][policy_id]['policy'] if policy_id else inputs['snapshot']['policy']
        evaluation = audit.evaluate_policy(event, policy)
        evaluations[event['id']] = evaluation
        if event['status'] in audit.SUCCESS and evaluation['decision'] == 'violation':
            signals.append({'event_id': event['id'], 'boundaries': evaluation['boundaries']})
    matched = evaluations == window['policy_evaluations'] and signals == window['signals']
    return {'oracle': 'monitor-window-policy-reconstruction-v1', 'window_id': window['id'],
            'executor_pid': os.getpid(), 'input_digest': window['input_digest'], 'event_count': len(events), 'signal_count': len(signals),
            'status': 'reproduced' if matched else 'mismatch', 'verification_status': 'human_review',
            'behavior_authenticity_verified': False, 'canonical_finding_created': False,
            'boundary': 'Separate-process deterministic policy reconstruction only; no independent observation or behavior replay.'}


def main():
    raw = sys.stdin.buffer.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('window_too_large')
    result = reconstruct(json.loads(raw))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
