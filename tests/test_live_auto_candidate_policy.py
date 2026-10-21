"""Opt-in actual scanner/model/policy handoff using public loopback fixtures only."""
import os
import pytest
from tests.test_final import client
from tests.test_live_tool_model_research import test_actual_scanner_observations_reach_real_model_without_injected_claims as run_case

pytestmark=pytest.mark.skipif(os.getenv('FIELDWORK_LIVE_AUTO_CANDIDATES')!='1',reason='explicit actual scanner, paid model and automatic candidate acceptance')


@pytest.mark.parametrize('fixed',[False,True])
def test_actual_model_drafts_reach_policy_candidates_without_manual_handoff(client,monkeypatch,tmp_path,fixed):
    run_case(client,monkeypatch,tmp_path,fixed,automatic=True)
