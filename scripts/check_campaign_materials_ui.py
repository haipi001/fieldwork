"""Real Campaign APIs with isolated registered-source fixtures; no tools or models."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
import json,os,secrets,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT)]
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app,final_core
from tests.test_campaign_materials import fixture_materials

with tempfile.TemporaryDirectory(prefix='fieldwork-campaign-materials-ui-') as directory,ExitStack() as stack:
    root=Path(directory).resolve()
    for module,key,value in [(app,'DB',root/'ui.db'),(app,'DATA',root),(final_core,'DB',root/'ui.db'),(final_core,'LOCAL_DATA_ROOT',root)]:stack.enter_context(patch.object(module,key,value))
    token=secrets.token_urlsafe(48)
    stack.enter_context(patch.dict(os.environ,{'FIELDWORK_SESSION_TOKEN':token,'FIELDWORK_DISABLE_AGENT_MONITOR':'1','FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER':'1'}))
    client=stack.enter_context(TestClient(app.app,base_url='http://127.0.0.1:8000',headers={'X-Fieldwork-Session':token}))
    project,old,run,path,aid,oid,plan_url=fixture_materials(client)
    with final_core.connect() as db:db.execute('DELETE FROM research_campaigns WHERE id=?',(old['id'],))
    errors=[];writes=[];plans=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(route):
            req=route.request;url=urlparse(req.url);assert url.hostname=='127.0.0.1'
            if url.path.startswith('/static/'):route.fulfill(path=str(ROOT/url.path.lstrip('/')));return
            if url.path=='/api/v1/task-center':route.fulfill(json=[]);return  # Selected Run exists only in the project-specific loader.
            if req.method!='GET':writes.append(url.path)
            if url.path.endswith('/campaign-source-plan'):plans.append(url.path)
            result=client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=result.status_code,body=result.content,content_type=result.headers.get('content-type','application/json'))
        page.route('**/*',route);page.goto('http://127.0.0.1:8000/v5#agents')
        page.locator('#teamEngagement option[value="'+project['id']+'"]').wait_for(state='attached')
        page.locator('#teamEngagement').select_option(project['id'])
        page.locator('#teamRun option[value="'+run+'"]').wait_for(state='attached')
        page.locator('#teamRun').select_option(run)
        page.locator('#teamNextStep').click()
        page.locator('[data-campaign-source-run]').wait_for()
        assert page.locator('[data-campaign-source-run]').input_value()==run
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('反证 1')")
        assert not writes
        with final_core.connect() as db:assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==0
        page.locator('#researchCampaignName').fill('Reviewed late Campaign')
        page.locator('#researchCampaignObjective').fill('Review only this Run recorded evidence and negative controls')
        initial_plans=len(plans);page.evaluate("document.querySelector('#languageToggle').click()")
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('counterevidence 1')")
        assert page.locator('#researchCampaignName').input_value()=='Reviewed late Campaign'
        assert page.locator('[data-campaign-source-run]').input_value()==run
        assert len(plans)==initial_plans and not writes
        original_bytes=path.read_bytes();path.write_text('changed source bytes')
        page.locator('[data-campaign-source-refresh]').click()
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('Registered artifact bytes changed')")
        page.evaluate("document.querySelector('#languageToggle').click()")
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('登记产物文件已变化')")
        assert page.locator('#researchCampaignName').input_value()=='Reviewed late Campaign'
        assert page.locator('[data-campaign-source-run]').input_value()==run
        path.write_bytes(original_bytes);page.locator('[data-campaign-source-refresh]').click()
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('反证 1')")
        page.evaluate("document.querySelector('#languageToggle').click()")
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('counterevidence 1')")
        with final_core.connect() as db:db.execute("UPDATE observations SET summary='Changed before confirmation' WHERE id=?",(oid,))
        page.locator('#researchCampaignForm [type=submit]').click()
        page.wait_for_function("document.querySelector('#researchCampaignMessage')?.textContent.includes('Run materials changed')")
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM research_campaigns').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM research_nodes').fetchone()[0]==0
        page.locator('[data-campaign-source-refresh]').click()
        page.wait_for_function("document.querySelector('[data-campaign-source-status]')?.textContent.includes('counterevidence 1')")
        page.locator('#researchCampaignForm [type=submit]').click()
        page.wait_for_function("!document.querySelector('#researchCampaignDialog').open")
        with final_core.connect() as db:
            campaign=dict(db.execute('SELECT * FROM research_campaigns').fetchone())
            assert db.execute('SELECT count(*) FROM research_nodes WHERE campaign_id=?',(campaign['id'],)).fetchone()[0]==3
            assert db.execute("SELECT count(*) FROM research_nodes WHERE campaign_id=? AND node_type='counterevidence'",(campaign['id'],)).fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM agent_tasks').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        page.wait_for_function("id=>document.querySelector('#teamCampaign')?.value===id",arg=campaign['id'])
        assert not errors,errors
        browser.close()
    print(json.dumps({'page_errors':errors,'creation_posts':len(writes),'material_preview_gets':len(plans),'linked_nodes':3,'counterevidence':1,'model_calls':0,'tasks_started':0,'scope':'real APIs, isolated registered fixture; not actual scanning or model discovery'}))
