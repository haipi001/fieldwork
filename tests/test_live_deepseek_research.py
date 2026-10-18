"""Explicit opt-in, paid service acceptance using synthetic public graph inputs only."""
import json
import os
from pathlib import Path

import pytest
import final_core
import v5_runtime as runtime
import v5_research_worker as research
from tests.test_final import client
from tests.test_v5_research_worker import team, create

pytestmark = pytest.mark.skipif(os.environ.get('FIELDWORK_LIVE_DEEPSEEK') != '1',
                                reason='requires explicit live service acceptance')


def _configured_provider():
    import traditional_tools
    values = traditional_tools.strix_provider_settings()
    if values.get('LLM_API_BASE') != 'https://api.deepseek.com':
        pytest.skip('expected saved DeepSeek service unavailable')
    return runtime.create_provider(runtime.ProviderCreate(name='Live acceptance', location='cloud',
        base_url='https://api.deepseek.com', model=values['STRIX_LLM'].removeprefix('openai/'),
        api_key=values.get('LLM_API_KEY') or values.get('OPENAI_API_KEY'),
        metadata=runtime.ProviderMetadata(cost_micros_per_million_tokens=10000000)))


def test_live_public_team_call_settles_and_preserves_draft_boundary(client, monkeypatch, tmp_path):
    import runtime_secrets
    monkeypatch.setattr(runtime_secrets, 'secret_root', lambda: tmp_path / 'secrets')
    provider = _configured_provider()
    health = runtime.test_provider({'provider_id': provider['id']})
    assert health['status'] == 'healthy'
    profile = runtime.create_profile(runtime.ProfileCreate(name='Live acceptance', mode='cloud',
        config=runtime.ProfileConfig(cloud_provider_ids=[provider['id']], max_tokens_per_call=8000,
                                     max_tokens_per_hour=8000, max_concurrent_calls=1)))
    campaign, body = team(client, count=1)
    body.update(runtime_profile_id=profile['id'], allow_cloud_context=True,
                max_cost_micros=100000, max_tokens_per_task=8000)
    with final_core.connect() as db:
        db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',
                   (json.dumps({'sensitivity':'public'}), campaign['id']))
    group = create(client, body)
    response = client.post(f'/api/v1/workers/research/groups/{group}/start')
    assert response.status_code == 202, response.text
    research._jobs[group].join(timeout=55)
    assert not research._jobs[group].is_alive()
    with final_core.connect() as db:
        task = db.execute('SELECT status,result_json,error_json FROM agent_tasks WHERE group_id=?', (group,)).fetchone()
        calls = [dict(row) for row in db.execute('SELECT state,reserved_tokens FROM runtime_calls')]
        usage = [dict(row) for row in db.execute('SELECT input_tokens,output_tokens,cost_micros FROM runtime_usage')]
        finding_count = db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]
        report = {'service':'DeepSeek','model':provider['model'],'task':dict(task),
                  'calls':calls,'usage':usage,'canonical_findings':finding_count,
                  'data':'synthetic public fixture','cost_rate':'conservative test bound 10 USD / million tokens, not invoice pricing'}
    Path('build/live-deepseek-research.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    assert task['status'] == 'succeeded', report
    assert len(calls) == 1 and calls[0]['state'] == 'settled'
    assert len(usage) == 1 and usage[0]['input_tokens'] > 0 and usage[0]['output_tokens'] > 0
    assert finding_count == 0
