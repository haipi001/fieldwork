"""Exercise workspace UI against real APIs in an isolated database, with no target/model calls."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
import json,os,secrets,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app,final_core
from tests.test_v5_team_plan import _setup,_body

with tempfile.TemporaryDirectory(prefix='fieldwork-workspace-ui-') as directory,ExitStack() as stack:
    root=Path(directory)
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
    delayed={'checkpoint':False,'campaign_path':None,'routes':[],'monitor_state':None}
    def route_request(route):
        req=route.request;p=urlparse(req.url);assert p.hostname=='127.0.0.1',req.url
        if p.path=='/api/v1/workspace/monitor-process' and delayed['monitor_state'] is not None:
            state=delayed['monitor_state']
            if state=='unavailable':route.fulfill(status=503,json={'detail':'Isolated unavailable-state fixture'})
            else:route.fulfill(json={'state':state,'running':state!='stopped','pid':123,'version':'fixture','last_tick':1,'error':None})
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
        check=page.locator('[data-w-policy-check]');check.locator('[name="engagement_id"]').select_option(eng['id']);check.locator('button').click()
        page.wait_for_function("document.querySelector('#agentsContent [data-w-message]')?.textContent.includes('本地策略回归通过')")
        assert '12' in page.locator('[data-w-policy-result] table').inner_text()
        assert page.locator('[data-w-action="policy-evidence"]').is_visible()
        with final_core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM eval_runs_v6').fetchone()[0]==1
        page.screenshot(path=str(ROOT/'build/policy-check-ui.png'),full_page=True)
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
        assert 'required_bytes' in page.locator('[data-w-migration-result]').inner_text()
        assert 'migration_available' in page.locator('[data-w-migration-result]').inner_text()
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
        page.wait_for_function("document.querySelector('[data-w-component-detail]')?.textContent.includes('previous_version')")
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
        for service_state in ('stopped','starting','current','degraded','unavailable'):
            delayed['monitor_state']=service_state
            monitor.locator('[data-w-action="refresh"]').click()
            page.locator('[data-w-monitor-service][data-state="'+service_state+'"]').wait_for()
            assert monitor.locator('[data-w-monitor-service]').inner_text().strip()
        delayed['monitor_state']=None
        monitor.locator('[data-w-action="refresh"]').click()
        page.locator('[data-w-monitor-service][data-state="stopped"]').wait_for()
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM agent_monitors WHERE status='active'").fetchone()[0]==0
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
        evidence.locator('[name="q"]').fill(evidence_item['id']);evidence.locator('[name="q"]').press('Tab')
        evidence.locator('[name="artifact_kind"]').fill(evidence_item['type']);evidence.locator('[name="artifact_kind"]').press('Tab')
        page.wait_for_function("id=>document.querySelector('#workspace-evidence table')?.textContent.includes(id)",arg=evidence_item['id'])
        evidence.locator('[data-w-action="relations-0"]').click()
        evidence.locator('[data-w-relations] h3').wait_for()
        assert '观察记录' in evidence.locator('[data-w-relations]').inner_text()
        assert '未重新校验' in evidence.locator('[data-w-message]').inner_text()
        page.locator('[data-view="incidents"]').click()
        investigation=page.locator('#workspace-incidents')
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
