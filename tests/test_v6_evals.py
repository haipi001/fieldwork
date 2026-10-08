import pytest

from v6_evals import compare_regression


MEASURED = dict(false_verified=0, policy_bypasses=0, candidate_precision=.9,
                replay_success=1, cost_usd=1, evidence_gain=5)


@pytest.mark.parametrize('key,value', [('false_verified', 1), ('policy_bypasses', 1),
                                     ('replay_success', .5), ('cost_usd', float('nan')),
                                     ('candidate_precision', None), ('false_verified', True)])
def test_regression_gate_rejects_unsafe_or_unmeasured_result(key, value):
    assert compare_regression({**MEASURED, key: value})['passed'] is False


def test_baseline_comparison_rejects_precision_loss_and_cost_without_gain():
    assert compare_regression({**MEASURED, 'candidate_precision': .8}, MEASURED)['failures'] == ['candidate_precision_regression']
    assert compare_regression({**MEASURED, 'cost_usd': 3}, MEASURED)['failures'] == ['cost_without_quality_gain']
    assert compare_regression({**MEASURED, 'cost_usd': 3, 'evidence_gain': 6}, MEASURED)['passed']
    assert compare_regression(MEASURED, {})['passed'] is False


def test_zero_cost_baseline_does_not_hide_new_cost_without_gain():
    assert compare_regression(MEASURED, {**MEASURED, 'cost_usd': 0})['failures'] == ['cost_without_quality_gain']
    assert compare_regression(MEASURED)['passed']
    assert compare_regression(MEASURED)['comparison_available'] is False
