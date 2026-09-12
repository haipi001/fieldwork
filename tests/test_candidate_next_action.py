import pytest
from candidate_quality import candidate_next_action


@pytest.mark.parametrize('mode,category,status,evidence,stage,method', [
    ('traditional','access_control','candidate',['e1'],'supported','http_workbench'),
    ('traditional','access_control','candidate',[],'needs_evidence','http_workbench'),
    ('traditional','access_control','needs_evidence',['e1'],'needs_evidence','http_workbench'),
    ('traditional','business/offering','candidate',['e1'],'triage','manual_review'),
    ('traditional','ssrf','candidate',['e1'],'manual','manual_review'),
    ('web3','web3_property_violation','candidate',['e1'],'supported','web3_property'),
    ('web3','web3_property_violation','reproduced',['e1'],'impact','web3_property'),
    ('web3','compiler_state_interaction','candidate',['e1'],'manual','manual_review'),
])
def test_action_plan_is_advisory_and_respects_missing_evidence(mode,category,status,evidence,stage,method):
    candidate = dict(mode=mode,category=category,status=status,evidence_ids=evidence)
    original = candidate.copy()
    plan = candidate_next_action(candidate)
    assert plan['stage'] == stage and plan['method'] == method
    assert plan['materials'] and plan['automatic_execution'] is False
    assert candidate == original
