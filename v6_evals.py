"""Deterministic regression gates over measured metrics; this module dispatches no work."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RegressionGates:
    max_precision_drop: float = .05
    min_replay_success: float = .95
    max_cost_multiplier: float = 2.0


REQUIRED_METRICS = {
    'false_verified': 'count', 'policy_bypasses': 'count',
    'candidate_precision': 'rate', 'replay_success': 'rate',
    'cost_usd': 'amount', 'evidence_gain': 'amount',
}


def _validate(metrics: Mapping[str, float]) -> list[str]:
    failures = []
    for name, kind in REQUIRED_METRICS.items():
        value = metrics.get(name)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
                or kind == 'rate' and value > 1
                or kind == 'count' and value != int(value)):
            failures.append(f'invalid_metric:{name}')
    return failures


def compare_regression(current: Mapping[str, float], baseline: Mapping[str, float] | None = None,
                       gates: RegressionGates = RegressionGates()) -> dict:
    """Missing/non-finite measurements fail; they never become zero or a passing rate."""
    if (not 0 <= gates.max_precision_drop <= 1 or not 0 <= gates.min_replay_success <= 1
            or not math.isfinite(gates.max_cost_multiplier) or gates.max_cost_multiplier < 1):
        raise ValueError('invalid regression gates')
    failures = _validate(current)
    if baseline is not None:
        failures += [f'baseline:{reason}' for reason in _validate(baseline)]
    if failures:
        return {'passed': False, 'failures': failures, 'comparison_available': False}
    if current['false_verified'] > 0:
        failures.append('false_verified')
    if current['policy_bypasses'] > 0:
        failures.append('policy_bypass')
    if current['replay_success'] < gates.min_replay_success:
        failures.append('replay_success')
    if baseline is not None:
        if baseline['candidate_precision'] - current['candidate_precision'] > gates.max_precision_drop + 1e-12:
            failures.append('candidate_precision_regression')
        if (current['cost_usd'] > baseline['cost_usd'] * gates.max_cost_multiplier
                and current['evidence_gain'] <= baseline['evidence_gain']):
            failures.append('cost_without_quality_gain')
    return {'passed': not failures, 'failures': failures,
            'comparison_available': baseline is not None}
