"""Exercise workspace UI against real APIs in an isolated database, with no target/model calls."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
import json,os,secrets,sys,tempfile,time
ROOT=Path(__file__).resolve().parents[1];sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app,final_core
from tests.test_v5_team_plan import _setup,_body

with tempfile.TemporaryDirectory(prefix='fieldwork-workspace-ui-') as directory,ExitStack() as stack:
    root=Path(directory).resolve()
    for module,name,value in ((app,'DB',root/'ui.db'),(app,'DATA',root),(final_core,'DB',root/'ui.db'),(final_core,'LOCAL_DATA_ROOT',root)):
        stack.enter_context(patch.object(module,name,value))
    token=secrets.token_urlsafe(48);stack.enter_context(patch.dict(os.environ,{'FIELDWORK_SESSION_TOKEN':token,'FIELDWORK_DISABLE_AGENT_MONITOR':'1','FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER':'1'}))
    app.prepare_database_upgrade(root/'ui.db',root/'backups',ROOT);app.init_db();final_core.init_final_db();app.apply_v5_schema(root/'ui.db');app.finalize_database_version(root/'ui.db')
    client=stack.enter_context(TestClient(app.app,base_url='http://127.0.0.1:8000',headers={'X-Fieldwork-Session':token}))
    eng,campaign,run=_setup(client);body=_body(campaign,run,2)
    preview=client.post('/api/v1/orchestration/groups/team/preview',json=body).json()
    created=client.post('/api/v1/orchestration/groups/team',json={**body,'preview_hash':preview['preview_hash'],'idempotency_key':'workspace-browser-team'})
    assert created.status_code==201,created.text
    demo=client.post('/api/v1/agent-audit/demo')
    assert demo.status_code==201,demo.text
    evidence_item=client.get('/api/v1/workspace/records/evidence',params={'engagement_id':demo.json()['id']}).json()['items'][0]
    from tests.test_guided_research import candidate as investigation_fixture
    investigation_run, investigation_candidate=investigation_fixture(client)
    investigation_id=investigation_candidate['id']
    path_nodes=[]
    for title in ['Path entry','Path middle']+[f'Path filler {i}' for i in range(103)]+['Path distant target']:
        response=client.post('/api/v1/research/nodes',json={'campaign_id':campaign['id'],'node_type':'observation','title':title})
        assert response.status_code==201,response.text
        path_nodes.append(response.json()['id'])
    for source,target in [(path_nodes[0],path_nodes[1]),(path_nodes[1],path_nodes[-1])]:
        assert client.post('/api/v1/research/edges',json={'campaign_id':campaign['id'],'source_id':source,'target_id':target,'relation_type':'related_to'}).status_code==201
    errors=[]
    import monitor_startup
    login_directory=root/'fake-home/Library';login_directory.mkdir(parents=True)
    login_label='com.fieldwork.monitor.ui-'+__import__('hashlib').sha256(str(root).encode()).hexdigest()[:12]
    stack.enter_context(patch.object(monitor_startup,'_location',lambda:(login_label,login_directory/'LaunchAgents'/(login_label+'.plist'))))
    login_state={'registered':False}
    from types import SimpleNamespace
    def login_command(*args):
        if args[0]=='print':return SimpleNamespace(returncode=0 if login_state['registered'] else 113,stderr=b'' if login_state['registered'] else b'Could not find service')
        if args[0]=='bootstrap':login_state['registered']=True
        if args[0]=='bootout':login_state['registered']=False
        return SimpleNamespace(returncode=0,stderr=b'')
    stack.enter_context(patch.object(monitor_startup,'_command',login_command))
    delayed={'checkpoint':False,'campaign_path':None,'routes':[],'monitor_state':None}
    def route_request(route):
        req=route.request;p=urlparse(req.url);assert p.hostname=='127.0.0.1',req.url
        if p.path=='/api/v1/workspace/monitor-process' and delayed['monitor_state'] is not None:
            state=delayed['monitor_state']
            if state=='unavailable':route.fulfill(status=503,json={'detail':'Isolated unavailable-state fixture'})
            else:route.fulfill(json={'state':state,'running':state!='stopped','pid':123,'version':'fixture','last_tick':1,'error':'OSError' if state=='backoff' else None,'failures':3 if state=='backoff' else 0,'retry_after':time.time()+30 if state=='backoff' else None})
            return
        if delayed['campaign_path']==p.path:
            delayed['routes'].append(route);return
        if delayed['checkpoint'] and p.path.startswith('/api/v1/workspace/tasks/') and p.path.endswith('/checkpoints'):
            delayed['routes'].append(route);return
        if p.path=='/v5':route.fulfill(body=client.get('/v5').content,content_type='text/html')
        elif p.path.startswith('/static/'):route.fulfill(path=str(ROOT/p.path.lstrip('/')))
        elif p.path in {'/api/v1/runtime/readiness','/api/v1/capabilities','/api/v1/traditional/provider'}:
            route.fulfill(status=503,json={'detail':'Outside workspace UI test'})
        else:
            res=client.request(req.method,p.path+('?' +p.query if p.query else ''),content=req.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=res.status_code,body=res.content,content_type=res.headers.get('content-type','application/json'))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/*',route_request)
        page.goto('http://127.0.0.1:8000/v5#agents');page.locator('#teamCreate').wait_for()
        page.locator('#teamEngagement').select_option(eng['id'])
        page.locator('#teamCampaign option',has_text=campaign['name']).wait_for(state='attached')
        page.locator('#teamCampaign').select_option(campaign['id'])
        size_button=page.locator('[data-team-size-load]')
        size_button.locator('..').locator('summary').click()
        size_button.click()
        size_form=page.locator('[data-team-size-editor] form')
        size_form.locator('[name="researcher"]').fill('3')
        size_form.locator('button').click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('队伍规模已更新')")
        size_button=page.locator('[data-team-size-load]')
        size_button.locator('..').locator('summary').click();size_button.click()
        assert page.locator('[data-team-size-editor] [name="researcher"]').input_value()=='3'
        from v5_research_worker import _claim, _stopping
        _stopping.clear()
        live=_claim(created.json()['group']['id'])
        assert live is not None
        size_button.click()
        page.wait_for_function("document.querySelector('[data-team-size-editor] [name=researcher]')?.min==='1'")
        page.locator('[data-team-size-editor] [name="researcher"]').fill('4')
        page.locator('[data-team-size-editor] form button').click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('队伍规模已更新')")
        page.wait_for_function("async (id)=>(await (await fetch('/api/v1/workspace/groups/'+id+'/size')).json()).roles.researcher===4",arg=created.json()['group']['id'])
        with final_core.connect() as db:
            assert dict(db.execute('SELECT * FROM agent_tasks WHERE id=?',(live['id'],)).fetchone())==dict(live)
            db.execute("UPDATE analysis_runs SET status='running' WHERE id=?",(run,))
        for tab in ('security','capabilities','memory','mcp','attack'):
            page.locator(f'[data-agent-tab="{tab}"]').click();page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('已读取')")
        page.locator('[data-agent-tab="capabilities"]').click()
        proposal=page.locator('[data-w-intent]');proposal.locator('..').locator('summary').click()
        for name,value in {'task_id':live['id'],'agent_id':'browser-capability-agent','capability':'network.request','operation':'read','resource':'https://team.example.test/object'}.items():
            proposal.locator('[name="'+name+'"]').fill(value)
        proposal.locator('[name="side_effect"]').select_option('false');proposal.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-grant] [name=intent_id]')?.value.startsWith('intent-')")
        grant=page.locator('[data-w-grant]');grant.locator('..').locator('summary').click();grant.locator('button').click()
        page.locator('[data-w-review]').wait_for();page.locator('[data-w-review]').click()
        page.wait_for_function("document.querySelector('[data-w-grant-review]')?.textContent.includes('reason_code')")
        assert 'approval_supported' in page.locator('[data-w-grant-review]').inner_text()
        page.locator('[data-w-approval-preview]').click()
        approval_form=page.locator('[data-w-approval]')
        assert approval_form.locator('[name="decision"]').input_value()==''
        assert not approval_form.locator('[name="confirmed"]').is_checked()
        approval_form.locator('[name="decision"]').select_option('approve')
        approval_form.locator('[name="reason"]').fill('Isolated browser reviewed this exact read intent')
        approval_form.locator('[name="confirmed"]').check()
        approval_form.locator('button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('人工决定已记录')")
        assert 'approve' in page.locator('#agentsContent [data-w-body]').inner_text()
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM capability_approvals_v6 WHERE decision='approve'").fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM capability_uses_v6').fetchone()[0]==0

        page.locator('[data-w-revoke]').wait_for();page.locator('[data-w-revoke]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('已撤销')")
        with final_core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM capability_revocations_v6').fetchone()[0]==1
        page.locator('[data-agent-tab="security"]').click();page.locator('[data-w-register]').locator('..').locator('summary').click()
        page.locator('[data-w-register] [name="name"]').fill('Browser registered identity');page.locator('[data-w-register] button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('已登记')")
        assert 'Browser registered identity' in page.locator('#agentsContent').inner_text()
        with final_core.connect() as db:
            memory_agent=db.execute("SELECT id FROM agent_registry_v5 WHERE name='Browser registered identity'").fetchone()[0]
        page.locator('[data-agent-tab="memory"]').click()
        memory=page.locator('[data-w-memory]');memory.locator('..').locator('summary').click()
        memory.locator('[name="agent_id"]').fill(memory_agent)
        memory.locator('[name="file"]').set_input_files({'name':'notes.md','mimeType':'text/plain','buffer':b'First note\napi_key=fixture-private-key'})
        page.wait_for_function("document.querySelector('[data-w-memory] [name=content]')?.value.startsWith('First note')")
        memory.locator('button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('已保存')")
        with final_core.connect() as db:
            first_memory=db.execute('SELECT id FROM agent_memory_snapshots_v5 ORDER BY created_at LIMIT 1').fetchone()[0]
        memory=page.locator('[data-w-memory]');memory.locator('..').locator('summary').click()
        memory.locator('[name="agent_id"]').fill(memory_agent);memory.locator('[name="source_name"]').fill('notes.md');memory.locator('[name="content"]').fill('Second note')
        memory.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-memory-view] [name=snapshot]')?.options.length===3")
        with final_core.connect() as db:
            second_memory=db.execute('SELECT id FROM agent_memory_snapshots_v5 ORDER BY created_at DESC LIMIT 1').fetchone()[0]
        compare=page.locator('[data-w-memory-view]');compare.locator('[name="snapshot"]').select_option(second_memory);compare.locator('[name="previous"]').select_option(first_memory);compare.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-memory-result]')?.textContent.includes('+Second note')")
        assert 'fixture-private-key' not in page.locator('[data-w-memory-result]').inner_text()
        for expected_state, button_text in [('retired','停用此快照'),('active','恢复保留状态')]:
            governance=page.locator('[data-w-memory-governance]')
            governance.locator('[name="reason"]').fill('Isolated governance acceptance')
            governance.get_by_role('button',name=button_text,exact=True).click()
            page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('治理状态已记录')")
            compare=page.locator('[data-w-memory-view]')
            compare.locator('[name="snapshot"]').select_option(second_memory)
            compare.locator('[name="previous"]').select_option(first_memory)
            compare.locator('button').click()
            page.locator('[data-w-memory-governance]').wait_for()
            result=client.get('/api/v1/workspace/memory-snapshots/'+second_memory).json()
            assert result['governance_state']==expected_state
            assert result['content']=='Second note' and result['trust']=='untrusted'
            assert result['automatic_model_context'] is False
            assert '+Second note' in page.locator('[data-w-memory-result]').inner_text()
        assert len(result['governance_history'])==2
        for i in range(51):
            response=client.post('/api/v1/workspace/memory-snapshots',json={'agent_id':memory_agent,'source_name':'paged-history.md','content':f'History {i}'})
            assert response.status_code==201,response.text
        page.locator('[data-agent-tab="memory"]').click()
        page.locator('[data-w-action="agent-next"]').click()
        page.locator('[data-w-action="agent-prev"]').wait_for()
        assert page.locator('#agentsContent [data-w-body] > .table-wrap').first.locator('tbody tr').count()==3
        filters=page.locator('[data-w-memory-filter]')
        filters.locator('[name="agent_id"]').fill(memory_agent)
        filters.locator('[name="q"]').fill(first_memory)
        filters.locator('button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-memory-filter] [name=q]')?.value==='"+first_memory+"' && document.querySelector('#agentsContent [data-w-body]')?.textContent.includes('查看与比较快照')")
        assert page.locator('#agentsContent [data-w-body] > .table-wrap').first.locator('tbody tr').count()==1
        assert page.locator('[data-w-action="agent-next"]').count()==0
        compare=page.locator('[data-w-memory-view]')
        compare.locator('[name="snapshot"]').select_option(first_memory)
        compare.locator('[name="previous_id"]').fill(second_memory)
        compare.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-memory-result]')?.textContent.includes('Second note')")


        page.locator('[data-agent-tab="mcp"]').click()
        page.locator('[data-w-action="dependencies"]').click()
        page.locator('[data-w-action="sbom"]').wait_for()
        assert 'fastapi' in page.locator('[data-w-dependencies]').inner_text()
        with page.expect_download() as dependency_download:
            page.locator('[data-w-action="sbom"]').click()
        dependency_download.value.save_as(str(ROOT/'build/browser-python-bom.json'))
        assert json.loads((ROOT/'build/browser-python-bom.json').read_text())['bomFormat']=='CycloneDX'
        page.set_viewport_size({'width':430,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('[data-agent-tab="attack"]').click()
        attack=page.locator('[data-w-attack-form]')
        attack.locator('[name="agent_id"]').fill(memory_agent)
        attack.locator('[name="max_cases"]').fill('3')
        attack.locator('button').click()
        page.locator('[data-w-attack-save]').wait_for(state='visible')
        page.wait_for_function("document.querySelector('[data-w-attack-save]')?.disabled===false")
        assert '等待可执行适配器' in page.locator('[data-w-attack-preview]').inner_text()
        attack.locator('[name="max_cases"]').fill('4')
        assert page.locator('[data-w-attack-save]').is_disabled()
        attack.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-attack-save]')?.disabled===false")
        page.locator('[data-w-attack-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('申请已保存')")
        with final_core.connect() as db:
            attack_row=db.execute("SELECT * FROM agent_attack_tests_v5 WHERE mode='reviewed_request'").fetchone()
            assert attack_row['status']=='awaiting_adapter' and json.loads(attack_row['result_json'])['max_cases']==4
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        page.locator('[data-w-attack-history] details summary').click()
        page.locator('[data-w-action="cancel-attack-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-attack-history]')?.textContent.includes('cancelled')")
        with final_core.connect() as db:
            assert db.execute('SELECT status FROM agent_attack_tests_v5 WHERE id=?',(attack_row['id'],)).fetchone()[0]=='cancelled'
        attack=page.locator('[data-w-attack-form]')
        attack.locator('[name="agent_id"]').fill(memory_agent)
        adapter=page.locator('[data-w-adapter]');adapter.locator('summary').click()
        adapter.locator('[data-w-adapter-read]').click()
        page.wait_for_function("document.querySelector('[data-w-adapter-current]')?.textContent.includes('configuration_sha256')")
        adapter_fixture_source=b"import json,sys,pathlib\nvalue=json.loads(sys.stdin.readline())\nif value.get('protocol')=='fieldwork.local-agent-tools-jsonl.v1':\n print(json.dumps({'type':'tool_call','id':'public','name':'fixture.read','arguments':{'resource':'fixture://public'}}),flush=True)\n reply=json.loads(sys.stdin.readline())\n print(json.dumps({'type':'final','response':reply['result']['receipt']}),flush=True)\n raise SystemExit(0)\npath=value.get('memory',{}).get('file_path')\nif path:\n p=pathlib.Path(path)\n memory=json.loads(p.read_text())\n memory['notes'].append('benign UI note')\n p.write_text(json.dumps(memory))\nprint(json.dumps({'response':'local fixture'}))\n"
        adapter.locator('[name="source_file"]').set_input_files({'name':'local-reviewed-fixture.py','mimeType':'text/x-python','buffer':adapter_fixture_source})
        adapter.locator('[name="enabled"]').check()
        adapter.locator('[data-w-adapter-form] button').click()
        page.wait_for_function("document.querySelector('[data-w-adapter-save]')?.disabled===false")
        assert 'source_code' not in adapter.locator('[data-w-adapter-preview]').inner_text()
        adapter.locator('[data-w-adapter-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('适配器配置已保存')")
        with final_core.connect() as db:
            from agent_attack_adapters import current as adapter_current
            configured=adapter_current(db,memory_agent)
            assert configured['enabled'] and configured['row']['sha256']
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        active_form=page.locator('[data-w-attack-form]')
        active_form.locator('[name="agent_id"]').fill(memory_agent)
        active_form.locator('[name="max_cases"]').fill('2')
        active_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-attack-save]')?.disabled===false")
        page.locator('[data-w-attack-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('申请已保存')")
        detail=page.locator('[data-w-attack-history] > details').first
        detail.locator(':scope > summary').click()
        dispatch_form=detail.locator('[data-w-dispatch]')
        dispatch_form.locator('[name="engagement_id"]').select_option(demo.json()['id'])
        dispatch_form.locator('[name="authorization_note"]').fill('Explicit isolated UI fixture source approval')
        dispatch_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-dispatch-start]')?.disabled===false")
        dispatch_form.locator('[name="timeout_seconds"]').fill('1')
        assert detail.locator('[data-w-dispatch-start]').is_disabled()
        dispatch_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-dispatch-start]')?.disabled===false")
        detail.locator('[data-w-dispatch-start]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('本地观察已记录')")
        with final_core.connect() as db:
            executed=db.execute("SELECT * FROM agent_attack_tests_v5 WHERE status='observed'").fetchone()
            assert executed is not None
            execution=json.loads(executed['result_json'])['execution']
            assert execution['case_count']==2 and execution['artifact_id']
            assert db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(execution['run_id'],)).fetchone()[0]==2
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        detail=page.locator('[data-w-attack-history] > details').first
        detail.locator(':scope > summary').click()
        detail.locator('[data-w-action="attack-evidence-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-attack-evidence]')?.textContent.includes('integrity_verified')")
        assert execution['run_id'] in detail.locator('[data-w-attack-evidence]').inner_text()
        assert page.locator('#agents').evaluate("element=>element.classList.contains('active')")
        memory_form=page.locator('[data-w-attack-form]')
        memory_form.locator('[name="agent_id"]').fill(memory_agent)
        memory_form.locator('[name="category"]').select_option('memory')
        memory_form.locator('[name="max_cases"]').fill('2')
        memory_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-attack-save]')?.disabled===false")
        page.locator('[data-w-attack-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('申请已保存')")
        memory_detail=page.locator('[data-w-attack-history] > details').first
        memory_detail.locator(':scope > summary').click()
        memory_dispatch=memory_detail.locator('[data-w-dispatch]')
        memory_dispatch.locator('[name="engagement_id"]').select_option(demo.json()['id'])
        memory_dispatch.locator('[name="timeout_seconds"]').fill('1')
        memory_dispatch.locator('[name="authorization_note"]').fill('Explicit isolated memory fixture approval')
        memory_dispatch.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-dispatch-start]')?.disabled===false")
        memory_detail.locator('[data-w-dispatch-start]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('本地观察已记录')")
        with final_core.connect() as db:
            row=db.execute("SELECT * FROM agent_attack_tests_v5 WHERE scenario_id='memory' AND status='observed'").fetchone()
            memory_execution=json.loads(row['result_json'])['execution']
            assert memory_execution['case_count']==2 and memory_execution['observation']['attack_specific_memory_mutation'] is False
        memory_detail=page.locator('[data-w-attack-history] > details').first
        memory_detail.locator(':scope > summary').click()
        memory_detail.locator('[data-w-action="attack-evidence-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-attack-evidence]')?.textContent.includes('memory_observation')")
        assert memory_execution['run_id'] in memory_detail.locator('[data-w-attack-evidence]').inner_text()
        tool_form=page.locator('[data-w-attack-form]')
        tool_form.locator('[name="agent_id"]').fill(memory_agent)
        tool_form.locator('[name="category"]').select_option('tool')
        tool_form.locator('[name="max_cases"]').fill('2')
        tool_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-attack-save]')?.disabled===false")
        page.locator('[data-w-attack-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('申请已保存')")
        tool_detail=page.locator('[data-w-attack-history] > details').first
        tool_detail.locator(':scope > summary').click()
        tool_dispatch=tool_detail.locator('[data-w-dispatch]')
        tool_dispatch.locator('[name="engagement_id"]').select_option(demo.json()['id'])
        tool_dispatch.locator('[name="timeout_seconds"]').fill('1')
        tool_dispatch.locator('[name="authorization_note"]').fill('Explicit synthetic tool broker fixture approval')
        tool_dispatch.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-dispatch-start]')?.disabled===false")
        assert 'fixture://public' in tool_detail.locator('[data-w-dispatch-plan]').inner_text()
        tool_detail.locator('[data-w-dispatch-start]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('本地观察已记录')")
        with final_core.connect() as db:
            row=db.execute("SELECT * FROM agent_attack_tests_v5 WHERE scenario_id='tool' AND status='observed'").fetchone()
            tool_execution=json.loads(row['result_json'])['execution']
            assert tool_execution['case_count']==2 and tool_execution['observation']['attack_specific_forbidden_tool_request'] is False
        tool_detail=page.locator('[data-w-attack-history] > details').first
        tool_detail.locator(':scope > summary').click()
        tool_detail.locator('[data-w-action="tool-checkpoints-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-tool-checkpoints]')?.textContent.includes('known_request_count')")
        assert tool_execution['run_id'] in tool_detail.locator('[data-w-tool-checkpoints]').inner_text()
        with final_core.connect() as db:
            row=db.execute("SELECT result_json FROM agent_attack_tests_v5 WHERE scenario_id='tool' AND status='observed'").fetchone()
            checkpoints=json.loads(row[0])['execution']['tool_checkpoints']
            assert len(checkpoints)==2 and all(record['response_delivery']=='written_to_pipe' for record in checkpoints)
        tool_detail.locator('[data-w-action="attack-evidence-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-attack-evidence]')?.textContent.includes('tool_observation')")
        assert tool_execution['run_id'] in tool_detail.locator('[data-w-attack-evidence]').inner_text()
        tool_detail.locator('[data-w-action="attack-review-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-attack-review]')?.textContent.includes('synthetic_observation_consistency_only')")
        review_text=tool_detail.locator('[data-w-attack-review]').inner_text()
        assert tool_execution['run_id'] in review_text
        assert '限定范围内未观察到信号' in review_text or 'No signal observed in the supported scope' in review_text
        tool_detail.locator('[data-w-attack-review] details > summary').click()
        assert 'not_observed_in_supported_scope' in tool_detail.locator('[data-w-attack-review]').inner_text()

        page.set_viewport_size({'width':430,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('[data-w-attack-form] [name="agent_id"]').fill(memory_agent)
        adapter=page.locator('[data-w-adapter]');adapter.locator('summary').click()
        adapter.locator('[data-w-adapter-read]').click()
        page.wait_for_function("document.querySelector('[data-w-adapter-current]')?.textContent.includes('configuration_sha256')")
        adapter.locator('[name="enabled"]').uncheck()
        adapter.locator('[data-w-adapter-form] button').click()
        page.wait_for_function("document.querySelector('[data-w-adapter-save]')?.disabled===false")
        adapter.locator('[data-w-adapter-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('适配器配置已保存')")
        with final_core.connect() as db:
            assert not adapter_current(db,memory_agent)['enabled']
        # Maintenance only: replace source, reject a stale restore, then restore disabled.
        with final_core.connect() as db:
            maintenance_before={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('analysis_runs','agent_attack_tests_v5','runtime_calls')}
            original_adapter=adapter_current(db,memory_agent);original_sha=original_adapter['row']['sha256']
        page.locator('[data-w-attack-form] [name="agent_id"]').fill(memory_agent)
        adapter=page.locator('[data-w-adapter]');adapter.locator('summary').click()
        adapter.locator('[data-w-action="adapter-versions"]').click()
        adapter.locator('[data-w-adapter-versions] tbody tr').wait_for()
        assert adapter.locator('[data-w-adapter-versions] tbody tr').count()==1
        replacement=adapter_fixture_source+b'# second stored source revision\n'
        adapter.locator('[name="source_file"]').set_input_files({'name':'source-revision-2.py','mimeType':'text/x-python','buffer':replacement})
        adapter.locator('[name="enabled"]').check()
        adapter.locator('[data-w-adapter-form] button').click()
        page.wait_for_function("document.querySelector('[data-w-adapter-save]')?.disabled===false")
        adapter.locator('[data-w-adapter-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('适配器配置已保存')")
        page.locator('[data-w-attack-form] [name="agent_id"]').fill(memory_agent)
        adapter=page.locator('[data-w-adapter]');adapter.locator('summary').click()
        adapter.locator('[data-w-action="adapter-versions"]').click()
        page.wait_for_function("document.querySelectorAll('[data-w-adapter-versions] tbody tr').length===2")
        old_version=adapter.locator('[data-w-adapter-versions] tbody tr').filter(has_text=original_sha)
        old_version.locator('button').click()
        adapter.locator('[data-w-restore-reviewed]').wait_for()
        assert adapter.locator('[data-w-restore-save]').is_disabled()
        assert '\nvalue=' in adapter.locator('[data-w-adapter-restore] pre').inner_text()
        page.set_viewport_size({'width':430,'height':900})
        if not page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'):
            page.screenshot(path=str(ROOT/'build/adapter-history-overflow.png'),full_page=True)
            print(page.locator('#agentsContent').locator('*').evaluate_all("items=>items.filter(e=>e.getClientRects().length&&e.getBoundingClientRect().right>innerWidth+2).map(e=>({tag:e.tagName,cls:e.className,text:e.textContent.slice(0,100),width:e.getBoundingClientRect().width})).slice(-15)"),flush=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        page.set_viewport_size({'width':1440,'height':1000})
        from tests.test_agent_attack_adapters import register as register_adapter
        with final_core.connect() as db:changed=adapter_current(db,memory_agent)
        assert register_adapter(client,'/api/v1/workspace/agent-attack-requests/adapters/'+memory_agent,replacement.decode(),True,changed['configuration_sha256']).status_code==200
        adapter.locator('[data-w-restore-reviewed]').check();adapter.locator('[data-w-restore-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('Adapter changed; reload before restoring a version')")
        adapter.locator('[data-w-action="adapter-versions"]').click()
        page.wait_for_function("document.querySelectorAll('[data-w-adapter-versions] tbody tr').length===2")
        old_version.locator('button').click();adapter.locator('[data-w-restore-reviewed]').wait_for()
        page.locator('[data-w-attack-form] [name="agent_id"]').fill('changed-agent-context')
        assert adapter.locator('[data-w-restore-save]').count()==0
        page.locator('[data-w-attack-form] [name="agent_id"]').fill(memory_agent)
        adapter.locator('[data-w-action="adapter-versions"]').click()
        page.wait_for_function("document.querySelectorAll('[data-w-adapter-versions] tbody tr').length===2")
        old_version.locator('button').click();adapter.locator('[data-w-restore-reviewed]').wait_for()
        adapter.locator('[data-w-restore-reviewed]').check();adapter.locator('[data-w-restore-save]').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('源码版本已恢复为未启用')")
        with final_core.connect() as db:
            restored_adapter=adapter_current(db,memory_agent)
            assert restored_adapter['row']['sha256']==original_sha and not restored_adapter['enabled']
            assert len(restored_adapter['history'])==2
            assert maintenance_before=={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in maintenance_before}
        check=page.locator('[data-w-policy-check]');check.locator('[name="engagement_id"]').select_option(eng['id']);check.locator('button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('本地策略回归通过')")
        assert '12' in page.locator('[data-w-policy-result] table').inner_text()
        assert page.locator('[data-w-action="policy-evidence"]').is_visible()
        with final_core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM eval_runs_v6').fetchone()[0]==1
        page.screenshot(path=str(ROOT/'build/policy-check-ui.png'),full_page=True)
        page.locator('[data-agent-tab="mcp"]').click()
        load_adapter=page.locator('[data-w-component-load]');load_adapter.locator('..').locator(':scope > summary').click()
        load_adapter.locator('[name="component_id"]').fill(restored_adapter['row']['id']);load_adapter.locator('button').click()
        page.locator('[data-w-action="component-adapter-source"]').wait_for()
        assert 'source_code' not in page.locator('[data-w-component-detail]').inner_text()
        page.locator('[data-w-action="component-adapter-source"]').click()
        page.wait_for_function("document.querySelector('[data-w-component-source] pre')?.textContent.includes('import json,sys,pathlib')")
        assert adapter_fixture_source.decode().strip() in page.locator('[data-w-component-source] pre').inner_text()

        page.locator('[data-view="tasks"]').click();host=page.locator('#workspace-tasks')
        other_project=host.locator('[name="project"] option').evaluate_all('(items)=>items.map(item=>item.value)').copy()
        other_project=next(value for value in other_project if value and value!=eng['id'])
        delayed['campaign_path']='/api/v1/engagements/'+eng['id']+'/campaigns'
        host.locator('[name="project"]').select_option(eng['id'])
        page.wait_for_timeout(150)
        assert len(delayed['routes'])==1
        host.locator('[name="project"]').select_option(other_project)
        page.wait_for_timeout(150)
        delayed['campaign_path']=None
        delayed['routes'].pop().fulfill(json=client.get('/api/v1/engagements/'+eng['id']+'/campaigns').json())
        page.wait_for_timeout(150)
        assert host.locator('[name="project"]').input_value()==other_project
        assert host.locator('[name="campaign"] option[value="'+campaign['id']+'"]').count()==0
        host.locator('[name="project"]').select_option(eng['id']);host.locator('[name="campaign"] option',has_text=campaign['name']).wait_for(state='attached');host.locator('[name="campaign"]').select_option(campaign['id'])
        pending_action=host.locator('[data-w-action^="pause-"]').first.get_attribute('data-w-action');pending_index=pending_action.split('-')[1]
        host.locator('[data-w-action="detail-'+pending_index+'"]').click();host.locator('[name="priority"]').fill('42');host.locator('[data-w-priority] button').click();page.wait_for_function("document.querySelector('#workspace-tasks [data-w-message]').textContent.includes('已更新')")
        delayed['checkpoint']=True
        host.locator('[data-w-action^="detail-"]').first.click()
        page.wait_for_timeout(150)
        assert len(delayed['routes'])==1
        host.locator('[name="campaign"]').select_option('')
        host.locator('[name="campaign"]').select_option(campaign['id'])
        host.locator('[data-w-action^="pause-"]').first.wait_for()
        delayed['checkpoint']=False
        delayed['routes'].pop().fulfill(json={'items':[{'checkpoint':{'marker':'STALE-CHECKPOINT-SHOULD-NOT-RENDER'}}]})
        page.wait_for_timeout(150)
        assert 'STALE-CHECKPOINT-SHOULD-NOT-RENDER' not in host.inner_text()
        assert host.locator('[data-w-detail]').inner_text()==''
        host.locator('[data-w-action^="pause-"]').first.click();host.locator('[data-w-action^="resume-"]').first.wait_for()
        page.locator('[data-view="settings"]').click();page.locator('#workspace-settings [data-w-action="backup"]').click();page.locator('[data-w-action="drill-0"]').wait_for();page.locator('[data-w-action="drill-0"]').click();page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]').textContent.includes('通过')")
        migration_target=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='fieldwork-migration-ui-'))).resolve()
        migration=page.locator('[data-w-migration]')
        migration.locator('[name="destination"]').fill(str(migration_target))
        migration.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]')?.textContent.includes('目录检查已完成')")
        assert '目录条件通过' in page.locator('[data-w-migration-result]').inner_text()
        page.locator('[data-w-migration-result] summary').click()
        assert 'required_bytes' in page.locator('[data-w-migration-result]').inner_text()
        assert 'migration_available' in page.locator('[data-w-migration-result]').inner_text()
        migration_target.chmod(0o755)
        migration.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-migration-result]').textContent.includes('目录条件未通过')")
        assert '权限 700' in page.locator('[data-w-migration-result]').inner_text()
        migration_target.chmod(0o700)
        assert list(migration_target.iterdir())==[]
        migration.locator('[name="destination"]').fill('relative-directory')
        migration.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]')?.textContent.includes('absolute_directory_required')")
        page.locator('#workspace-settings [data-w-action="evidence-backup"]').click()
        page.locator('[data-w-action="evidence-drill-0"]').wait_for()
        page.locator('[data-w-action="evidence-drill-0"]').click()
        page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]')?.textContent.includes('隔离恢复校验通过')")
        page.locator('[data-w-action="restore-plan-0"]').click()
        page.locator('[data-w-restore-plan] table').filter(has_text='记录类型').wait_for()
        assert 'capability_approvals_v6' in page.locator('[data-w-restore-plan]').inner_text()
        assert '授权上下文对照' in page.locator('[data-w-restore-plan]').inner_text()
        assert '模型用量与执行配置对照' in page.locator('[data-w-restore-plan]').inner_text()
        assert 'runtime_usage' in page.locator('[data-w-restore-plan]').inner_text()

        assert '当前新增但备份没有' in page.locator('[data-w-restore-plan]').inner_text()
        with page.expect_download() as exported:
            page.locator('[data-w-export] button').click()
        exported.value.save_as(str(root/'record-index.json'))
        index=json.loads((root/'record-index.json').read_text())
        assert index['format']=='fieldwork-record-index-v1' and not index['restorable']
        assert index['counts']['tasks']>=3 and index['counts']['artifacts']>=1
        assert all('uri' not in item for item in index['records']['artifacts'])

        for view in ('events','evidence','incidents','runners','extensions','graph','sentinel'):
            page.locator(f'[data-view="{view}"]').click();page.locator('#workspace-'+view).wait_for();page.wait_for_timeout(120)
        page.locator('[data-view="extensions"]').click()
        extension=page.locator('#workspace-extensions')
        import base64
        from tests.test_extension_packages import package as extension_fixture
        package_one=extension_fixture();package_two=extension_fixture('2')
        package_form=extension.locator('[data-w-extension-preview]')
        for body in [package_one,package_two]:
            package_form.locator('[name="package"]').set_input_files({'name':'fixture.zip','mimeType':'application/zip','buffer':base64.b64decode(body['package_base64'])})
            package_form.locator('[name="sha256"]').fill(body['expected_sha256'])
            package_form.locator('button').click()
            package_install=extension.locator('[data-w-extension-install]')
            package_install.wait_for()
            assert not package_install.locator('[name="confirmed"]').is_checked()
            package_install.locator('[name="confirmed"]').check()
            package_install.locator('button').click()
            page.wait_for_function("document.querySelector('[data-w-extension-list]')?.textContent.includes('"+body['expected_sha256']+"')")
        extension.locator('[data-w-action="extension-versions-0"]').click()
        extension.locator('[data-w-action="extension-restore-1"]').click()
        page.wait_for_function("document.querySelector('[data-w-extension-list]')?.textContent.includes('"+package_one['expected_sha256']+"')")
        assert 'stored_disabled' in extension.locator('[data-w-extension-list]').inner_text()
        page.locator('[data-view="settings"]').click()
        package_settings=page.locator('#workspace-settings')
        package_settings.locator('[data-w-action="evidence-backup"]').click()
        page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]')?.textContent.includes('数据库与证据备份已保存')")
        package_archive=next(item for item in client.get('/api/v1/workspace/maintenance').json()['evidence_archives'] if item.get('extension_file_count')==3)
        package_settings.locator('tr').filter(has_text=package_archive['id']).locator('[data-w-action^="evidence-drill-"]').click()
        page.wait_for_function("document.querySelector('#workspace-settings [data-w-message]')?.textContent.includes('包库文件：3')")
        page.locator('[data-view="extensions"]').click()
        extension=page.locator('#workspace-extensions')
        extension.locator('summary',has_text='检索已登记组件').wait_for()


        revision_agent=client.post('/api/v1/workspace/agents',json={'name':'Component revision browser fixture'}).json()['id']
        revision_component=client.post('/api/v1/workspace/components',json={'agent_id':revision_agent,'component_type':'mcp','name':'Component revision browser fixture','version':'1','source':'fixture:browser-old'}).json()['id']
        extension.locator('summary',has_text='检索已登记组件').click()
        component_filter=extension.locator('[data-w-component-filter]')
        component_filter.locator('[name="agent_id"]').fill(revision_agent)
        component_filter.locator('[name="q"]').fill('Component revision browser fixture')
        component_filter.locator('button').click()
        extension.locator('[data-w-action="component-open-0"]').click()
        load_component=extension.locator('[data-w-component-load]')
        assert load_component.locator('[name="component_id"]').input_value()==revision_component

        revise_component=extension.locator('[data-w-component-revise]')
        revise_component.locator('[name="version"]').fill('2')
        revise_component.locator('[name="source"]').fill('fixture:browser-new')
        revise_component.locator('[name="reason"]').fill('Browser version declaration revision')
        revise_component.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-extensions [data-w-message]')?.textContent.includes('声明已修订')")
        load_component.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-extensions [data-w-component-detail]')?.textContent.includes('previous_version')")
        assert 'fixture:browser-old' in extension.locator('[data-w-component-detail]').inner_text()
        for i in range(51):
            assert client.post('/api/v1/workspace/components',json={'agent_id':revision_agent,'component_type':'library','name':f'Browser inventory filler {i:02}','version':'1','source':f'fixture:inventory:{i}'}).status_code==201
        component_filter.locator('[name="q"]').fill('')
        component_filter.locator('button').click()
        extension.locator('[data-w-action="component-next"]').click()
        page.wait_for_function("document.querySelectorAll('[data-w-component-results] [data-w-action^=component-open-]').length===2")
        extension.locator('[data-w-action="component-prev"]').click()
        page.wait_for_function("document.querySelectorAll('[data-w-component-results] [data-w-action^=component-open-]').length===50")

        extension.locator('[data-w-action="pack-check-0"]').click()
        page.wait_for_function("document.querySelector('[data-w-pack-check]')?.textContent.includes('清单摘要')")
        assert 'traditional_runtime:create_http_exchange' in extension.locator('[data-w-pack-check]').inner_text()
        assert '未授权' in extension.locator('[data-w-pack-check]').inner_text()
        page.locator('[data-view="sentinel"]').click()
        monitor=page.locator('#workspace-sentinel')
        for service_state in ('stopped','starting','current','backoff','degraded','unavailable'):
            delayed['monitor_state']=service_state
            monitor.locator('[data-w-action="refresh"]').click()
            page.locator('[data-w-monitor-service][data-state="'+service_state+'"]').wait_for()
            assert monitor.locator('[data-w-monitor-service]').inner_text().strip()
            if service_state=='backoff':
                content=monitor.locator('[data-w-monitor-service]').inner_text()
                assert '等待重试' in content and '连续失败' in content and '下次重试' in content and 'OSError' in content
        delayed['monitor_state']=None
        monitor.locator('[data-w-action="refresh"]').click()
        page.locator('[data-w-monitor-service][data-state="stopped"]').wait_for()
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM agent_monitors WHERE status='active'").fetchone()[0]==0
        login_form=monitor.locator('[data-w-monitor-login]')
        login_form.locator('[name=enabled]').check();login_form.locator('[name=confirmed]').check();login_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-startup]')?.textContent.includes('登录任务已配置并注册')")
        assert login_state['registered'] is True
        login_form=monitor.locator('[data-w-monitor-login]')
        login_form.locator('[name=enabled]').uncheck();login_form.locator('[name=confirmed]').check();login_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-startup]')?.textContent.includes('尚未启用登录启动')")
        assert login_state['registered'] is False
        import monitor_process_control
        monitor_process_control._write('managed.json',{'service_id':'b'*32,'state':'starting'})
        monitor.locator('[data-w-action="refresh"]').click()
        page.locator('[data-w-monitor-service][data-state="unresolved"]').wait_for()
        monitor.locator('[data-w-action="monitor-recover"]').click()
        page.locator('[data-w-monitor-service][data-state="stopped"]').wait_for()
        assert monitor_process_control.should_stop('b'*32)
        start_form=monitor.locator('[data-w-monitor-start]')
        start_form.locator('[name="confirmed"]').check()
        start_form.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-sentinel [data-w-message]')?.textContent.includes('启动请求已提交')")
        import monitor_process_control
        process_identity=client.get('/api/v1/workspace/monitor-process').json().get('service_id')
        try:
            for _ in range(200):
                process_state=client.get('/api/v1/workspace/monitor-process').json()
                if process_state['state']=='current':break
                page.wait_for_timeout(100)
            assert process_state['state']=='current' and process_state['managed'],process_state
            monitor.locator('[data-w-action="refresh"]').click()
            page.locator('[data-w-monitor-service][data-state="current"]').wait_for()
            monitor.locator('[data-w-action="monitor-stop"]').click()
            for _ in range(200):
                process_state=client.get('/api/v1/workspace/monitor-process').json()
                if process_state['state']=='stopped':break
                page.wait_for_timeout(100)
            assert process_state['state']=='stopped',process_state
        finally:
            process_state=client.get('/api/v1/workspace/monitor-process').json()
            if process_state.get('managed') and process_state.get('service_id')==process_identity:
                client.post('/api/v1/workspace/monitor-process/stop',json={'service_id':process_identity})
            child=monitor_process_control._children.get(process_identity)
            if child:child.wait(timeout=20)
        with final_core.connect() as db:
            for i in range(103):
                db.execute('INSERT INTO agent_task_checkpoints VALUES(?,?,?,?,?)',(f'browser-history-{i:03}',live['id'],'fixture-runner',final_core.dump({'index':i}),'2026-10-11T00:00:00Z'))
        page.locator('[data-view="events"]').click()
        events=page.locator('#workspace-events')
        events.locator('[name="task_id"]').fill(live['id'])
        events.locator('[name="task_id"]').press('Tab')
        events.locator('[data-w-action^="task-context-"]').first.click()
        page.wait_for_timeout(300)
        page.wait_for_function("document.querySelector('#workspace-events [data-w-relations]')?.textContent.includes('"+live['id']+"')")
        assert '关联任务详情' in events.locator('[data-w-relations]').inner_text()
        assert '模型调用账本' in events.locator('[data-w-relations]').inner_text()
        events.locator('[data-w-action="cp-next"]').click()
        events.locator('[data-w-action="cp-prev"]').wait_for()
        assert events.locator('[data-w-action="cp-next"]').count()==0
        assert 'browser-history-102' in events.locator('[data-w-relations]').inner_text()

        page.locator('[data-view="runners"]').click()
        runner=page.locator('#workspace-runners')
        runner.locator('[data-w-action="admission"]').click()
        page.wait_for_function("document.querySelector('[data-w-admission] strong')?.textContent==='停止接收新任务'")
        assert client.get('/api/v1/workers/research/admission').json()['state']=='draining'
        runner.locator('[data-w-action="admission"]').click()
        page.wait_for_function("document.querySelector('[data-w-admission] strong')?.textContent==='允许新任务'")
        assert client.get('/api/v1/workers/research/admission').json()['state']=='accepting'
        page.locator('[data-view="events"]').click()
        events=page.locator('#workspace-events')
        events.locator('[name="source"]').select_option('orchestrator')
        events.locator('[name="task_id"]').fill(live['id']);events.locator('[name="task_id"]').press('Tab')
        page.wait_for_function("id=>document.querySelector('#workspace-events table')?.textContent.includes(id)",arg=live['id'])
        assert 'orchestrator' in events.locator('table').inner_text()
        page.locator('[data-view="evidence"]').click()
        evidence=page.locator('#workspace-evidence')
        evidence.locator('[name="project"]').select_option('')
        evidence.locator('[name="q"]').fill(evidence_item['id']);evidence.locator('[name="q"]').press('Tab')
        evidence.locator('[name="artifact_kind"]').fill(evidence_item['type']);evidence.locator('[name="artifact_kind"]').press('Tab')
        page.wait_for_function("id=>document.querySelector('#workspace-evidence table')?.textContent.includes(id)",arg=evidence_item['id'])
        evidence.locator('[data-w-action="relations-0"]').click()
        evidence.locator('[data-w-relations] h3').wait_for()
        assert '观察记录' in evidence.locator('[data-w-relations]').inner_text()
        assert '未重新校验' in evidence.locator('[data-w-message]').inner_text()
        page.locator('[data-view="incidents"]').click()
        investigation=page.locator('#workspace-incidents')
        investigation.locator('[name="project"]').select_option('')
        investigation.locator('[name="q"]').fill(investigation_id);investigation.locator('[name="q"]').press('Tab')
        page.wait_for_function("id=>document.querySelector('#workspace-incidents table')?.textContent.includes(id)",arg=investigation_id)
        investigation.locator('[data-w-action="investigate-0"]').click()
        triage=investigation.locator('[data-w-triage]');triage.wait_for()
        triage.locator('[name="reason"]').fill('Fixture has no independent evidence yet')
        triage.locator('button').click()
        page.wait_for_function("document.querySelector('#workspace-incidents [data-w-message]')?.textContent.includes('needs_evidence')")
        assert 'needs_evidence' in investigation.locator('[data-w-body] > .table-wrap > table').inner_text()
        assert client.get('/api/v1/candidates/'+investigation_id).json()['candidate']['status']=='needs_evidence'
        investigation.locator('[data-w-action="review-plan"]').click()
        dialog=page.locator('#workflowExecutionDialog');dialog.wait_for(state='visible')
        page.wait_for_function("!document.querySelector('#workflowExecutionDialog')?.textContent.includes('正在检查当前材料')")
        assert dialog.locator('[role=alert]').inner_text()==''
        assert investigation_candidate['title'] in dialog.inner_text()
        assert '授权快照' in dialog.inner_text()
        assert dialog.locator('#workflowExecute').is_disabled()
        assert dialog.locator('#workflowAuthorized').is_disabled()
        dialog.locator('[data-workflow-close]').click()
        with final_core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM guided_research_jobs').fetchone()[0]==0
        page.locator('[data-view="graph"]').click()
        paths=page.locator('#workspace-graph')
        paths.locator('[name="project"]').select_option(eng['id'])
        paths.locator('[name="campaign"] option',has_text=campaign['name']).wait_for(state='attached')
        paths.locator('[name="campaign"]').select_option(campaign['id'])
        paths.locator('[name="source"] option[value="'+path_nodes[0]+'"]').wait_for(state='attached')
        paths.locator('[name="source"]').select_option(path_nodes[0])
        assert paths.locator('[name="target"] option[value="'+path_nodes[-1]+'"]').count()==0
        paths.locator('[name="search_target"]').fill('Path distant target');paths.locator('[name="search_target"]').press('Tab')
        paths.locator('[name="target"] option[value="'+path_nodes[-1]+'"]').wait_for(state='attached')
        paths.locator('[name="target"]').select_option(path_nodes[-1])
        paths.locator('[data-w-node-picker="source"] [data-w-node-next]').click()
        page.wait_for_function("document.querySelector('[data-w-node-picker=source] [data-w-node-prev]')?.disabled===false")
        assert paths.locator('[name="source"]').input_value()==path_nodes[0]
        paths.locator('[data-w-path] [type="submit"]').click()
        page.wait_for_function("id=>document.querySelector('[data-w-paths]')?.textContent.includes(id)",arg=path_nodes[-1])
        assert path_nodes[1] in paths.locator('[data-w-paths]').inner_text()
        expected_views = ('campaign','graph','hypotheses','sentinel','events','incidents','agents','evolution','tasks','runners','tools','evidence','verification','findings','reports','runtime','extensions','settings')
        assert set(page.locator('.nav-item[data-view]').evaluate_all('(items)=>items.map(x=>x.dataset.view)')) == set(expected_views)
        for view in expected_views:
            page.locator(f'.nav-item[data-view="{view}"]').click()
            panel=page.locator('section.view#'+view)
            panel.wait_for(state='visible')
            assert panel.locator('h1:visible').first.inner_text().strip(),view
            assert len(panel.inner_text().strip())>20,view
        display_matrix=[]
        for language in ('zh','en'):
            if language=='en':
                page.set_viewport_size({'width':1440,'height':1000});page.locator('#languageToggle').click()
            for theme in ('light','dark'):
                page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
                for width in (1440,430):
                    page.set_viewport_size({'width':width,'height':1000})
                    for view in expected_views:
                        page.evaluate('(view)=>location.hash=view',view)
                        panel=page.locator('section.view#'+view);panel.wait_for(state='visible');page.wait_for_timeout(120)
                        assert panel.locator('h1:visible').first.inner_text().strip(),(view,language,theme,width)
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1'),(view,language,theme,width)
                        clipped=panel.locator('button,input,select,textarea').evaluate_all('''items=>items.filter(item=>item.getClientRects().length&&!item.closest('.table-wrap,.graph-canvas,[hidden]')).filter(item=>{const r=item.getBoundingClientRect();return r.left < -1 || r.right > innerWidth+1}).map(item=>({tag:item.tagName,id:item.id,name:item.name,text:item.textContent.slice(0,80)}))''')
                        assert not clipped,(view,language,theme,width,clipped)
                        display_matrix.append({'view':view,'language':language,'theme':theme,'width':width,'horizontal_overflow':False,'clipped_controls':[]})
        (ROOT/'build/workspace-display-matrix.json').write_text(json.dumps(display_matrix,ensure_ascii=False,indent=2))
        page.set_viewport_size({'width':1440,'height':1000});page.screenshot(path=str(ROOT/'build/workspace-ui-06866.png'),full_page=True)
        assert not errors,errors
        browser.close()
    with final_core.connect() as db:assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
    print('Workspace UI: 18 navigation pages, 5 Agent tabs, live team resizing, capability proposal/grant/revoke, memory import/compare, event filters, evidence relationships, candidate investigation/triage, registration, priority/pause, backup/restore drill, 8 surfaces and 144 page/display combinations passed; zero model calls.')
