"""Opt-in actual Nuclei -> graph -> paid DeepSeek. Local synthetic target only.

No scanner output, model answer, Claim or Finding is injected. The fixture
behavior is deliberate; this does not establish holdout discovery quality.
"""
import hashlib
import json
import os
from pathlib import Path
import pytest
import final_core as core
import v5_graph
import v5_runtime as runtime
import v5_research_worker as research
from tests.test_final import client
from tests.test_live_deepseek_research import _configured_provider
from tests.test_actual_scanner_discovery import test_real_nuclei_header_signal_and_repaired_control as run_scanner_case

pytestmark=pytest.mark.skipif(os.getenv('FIELDWORK_LIVE_TOOL_MODEL')!='1',reason='explicit paid actual scanner/model acceptance')


@pytest.mark.parametrize('fixed',[False,True])
def test_actual_scanner_observations_reach_real_model_without_injected_claims(client,monkeypatch,tmp_path,fixed):
    # Existing production tool runner/broker and parser, actual installed Nuclei.
    run_scanner_case(client,fixed,'deep',real_run=True)
    with core.connect() as db:
        run=dict(db.execute('SELECT * FROM analysis_runs ORDER BY rowid DESC LIMIT 1').fetchone())
        artifact=dict(db.execute("SELECT * FROM artifacts WHERE run_id=? AND kind='traditional.tool.nuclei' ORDER BY rowid DESC LIMIT 1",(run['id'],)).fetchone())
        observations=[dict(row) for row in db.execute('SELECT * FROM observations WHERE run_id=? AND raw_ref=?',(run['id'],artifact['id']))]
    assert observations and not run['synthetic']
    assert hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest()==artifact['sha256']
    campaign_response=client.post(f"/api/v1/engagements/{run['engagement_id']}/campaigns",json={
        'name':'Actual scanner/model acceptance','objective':'Review frozen recorded tool observations and their limitations'})
    assert campaign_response.status_code==201,campaign_response.text
    campaign=campaign_response.json()
    with core.connect() as db:
        v5_graph.project_traditional_tool_result(db,run['engagement_id'],run['id'],artifact['id'],[row['id'] for row in observations])
        # Only deliberate loopback fixture observations are approved for cloud.
        for row in db.execute('SELECT id,attributes_json FROM research_nodes WHERE campaign_id=?',(campaign['id'],)).fetchall():
            attributes=core.load(row['attributes_json'],{})|{'sensitivity':'public','acceptance_data':'synthetic loopback fixture'}
            db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(core.dump(attributes),row['id']))
        assert db.execute("SELECT count(*) FROM research_nodes WHERE campaign_id=? AND node_type='claim'",(campaign['id'],)).fetchone()[0]==0
    import runtime_secrets
    monkeypatch.setattr(runtime_secrets,'secret_root',lambda:tmp_path/'secrets')
    import v5_model_response
    decoder_diagnostics=[]
    original_decode=v5_model_response.decode_response
    def diagnose(raw,kind,runtime_ms):
        envelope=json.loads(raw);choice=envelope.get('choices',[{}])[0];message=choice.get('message',{});content=message.get('content')
        diagnostic={'finish_reason':choice.get('finish_reason'),'content_type':type(content).__name__,
                    'content_chars':len(content) if isinstance(content,str) else None,
                    'content_sha256':hashlib.sha256(content.encode()).hexdigest() if isinstance(content,str) else None,
                    'reasoning_chars':len(message.get('reasoning_content') or '')}
        if isinstance(content,str):
            try:json.loads(content)
            except json.JSONDecodeError as error:diagnostic.update(json_error=error.msg,line=error.lineno,column=error.colno)
        decoder_diagnostics.append(diagnostic)
        return original_decode(raw,kind,runtime_ms)
    monkeypatch.setattr(v5_model_response,'decode_response',diagnose)
    provider=_configured_provider()
    assert runtime.test_provider({'provider_id':provider['id']})['status']=='healthy'
    profile=runtime.create_profile(runtime.ProfileCreate(name='Actual scanner/model acceptance',mode='cloud',
        config=runtime.ProfileConfig(cloud_provider_ids=[provider['id']],max_tokens_per_call=8000,max_tokens_per_hour=8000,max_concurrent_calls=1)))
    body={'campaign_id':campaign['id'],'run_id':run['id'],'objective':'Review only the frozen tool observations; cite support, preserve counterevidence and limitations, and identify open questions.',
          'roles':[{'role':'researcher','count':1}],'max_concurrency':1,'max_cost_micros':100000,
          'runtime_profile_id':profile['id'],'allow_cloud_context':True,'max_tokens_per_task':8000}
    preview=client.post('/api/v1/orchestration/groups/team/preview',json=body)
    assert preview.status_code==200,preview.text
    created=client.post('/api/v1/orchestration/groups/team',json={**body,'preview_hash':preview.json()['preview_hash'],'idempotency_key':'actual-scanner-model'})
    assert created.status_code==201,created.text
    group=created.json()['group']['id']
    response=client.post(f'/api/v1/workers/research/groups/{group}/start');assert response.status_code==202,response.text
    research._jobs[group].join(timeout=55)
    assert not research._jobs[group].is_alive(),'Actual research task still running; do not start a duplicate'
    with core.connect() as db:
        task=dict(db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone())
        calls=[dict(r) for r in db.execute('SELECT state,reserved_tokens FROM runtime_calls')]
        usage=[dict(r) for r in db.execute('SELECT input_tokens,output_tokens,cost_micros FROM runtime_usage')]
        inputs=core.load(task['context_capsule_json'])['research_input_hashes']
        input_rows=[dict(db.execute('SELECT * FROM research_nodes WHERE id=?',(nid,)).fetchone()) for nid in inputs]
        claims=[dict(r) for r in db.execute("SELECT id,title,body,attributes_json FROM research_nodes WHERE campaign_id=? AND node_type='claim'",(campaign['id'],))]
        findings=db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]
    report={'case':'fixed' if fixed else 'positive','tool':'actual Nuclei deep via scoped broker','model':provider['model'],
            'tool_artifact_sha256':artifact['sha256'],'observations':[{'id':r['id'],'type':r['observation_type'],'summary':r['summary']} for r in observations],
            'input_hashes':inputs,'input_sources':[{'id':r['id'],'source_type':r['source_type'],'source_ref':r['source_ref'],'node_type':r['node_type']} for r in input_rows],
            'task_status':task['status'],'result':core.load(task['result_json'],{}),'error':core.load(task['error_json'],{}),
            'calls':calls,'usage':usage,'decoder_diagnostics':decoder_diagnostics,'claims':claims,'claim_count':len(claims),'canonical_findings':findings,
            'cost_rate':'conservative 10 USD/million token bound, not invoice','boundary':'Research draft only; no independent behavior verification or formal report; deliberate fixture, not holdout quality'}
    output=Path('build/actual-tool-model-20261018');output.mkdir(parents=True,exist_ok=True)
    case='fixed' if fixed else 'positive'
    (output/(case+'-tool-artifact.json')).write_bytes(Path(artifact['uri']).read_bytes())
    (output/(case+'-frozen-inputs.json')).write_text(json.dumps(input_rows,ensure_ascii=False,indent=2))
    (output/(case+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    assert task['status']=='succeeded',report
    assert any(r['node_type']=='observation' and r['source_type']=='observation' for r in input_rows)
    assert all(r['source_type']!='team_research' for r in input_rows)
    assert len(calls)==1 and calls[0]['state']=='settled'
    assert len(usage)==1 and usage[0]['input_tokens']>0 and usage[0]['output_tokens']>0
    assert findings==0
