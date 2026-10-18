"""Real package APIs + isolated persisted tool-result fixture; no scan or cloud model."""
from contextlib import ExitStack
from pathlib import Path
import json, os, secrets, sys, tempfile
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1];sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app,final_core
from tests.test_artifact_fingerprint_plan import setup

with tempfile.TemporaryDirectory(prefix='fieldwork-package-ui-') as directory, ExitStack() as stack:
    root=Path(directory).resolve()
    for module,key,value in [(app,'DB',root/'ui.db'),(app,'DATA',root),(final_core,'DB',root/'ui.db'),(final_core,'LOCAL_DATA_ROOT',root)]:
        stack.enter_context(patch.object(module,key,value))
    import traditional_tools
    stack.enter_context(patch.object(traditional_tools,'ARTIFACT_ROOT',root/'artifacts'))
    stack.enter_context(patch.dict(os.environ,{'FIELDWORK_SESSION_TOKEN':secrets.token_urlsafe(48)}))
    app.prepare_database_upgrade(root/'ui.db',root/'backups',ROOT);app.init_db();final_core.init_final_db();app.apply_v5_schema(root/'ui.db');app.apply_v6_schema(root/'ui.db');app.finalize_database_version(root/'ui.db')
    client=TestClient(app.app,base_url='http://127.0.0.1:8000',headers={'X-Fieldwork-Session':os.environ['FIELDWORK_SESSION_TOKEN']})
    campaign_id,artifact=setup(client)
    with final_core.connect() as db:db.execute('DELETE FROM intel_records')
    match={"campaign_id":campaign_id}
    errors=[];writes=[];flags={"wrong_receipt":False,"fail_receipt":False,"fail_tick":False,"fail_confirmation":False}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel="chrome",headless=True);page=browser.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(route):
            req=route.request;url=urlparse(req.url)
            assert url.hostname=='127.0.0.1'
            if url.path=='/':route.fulfill(body='<html lang="en"><body><div id="hypothesesContent"></div></body></html>',content_type='text/html');return
            assert url.path.startswith('/api/')
            if req.method!='GET':writes.append(url.path)
            if url.path=='/api/v1/verification/local/tick' and flags['fail_tick']:
                rid=parse_qs(url.query)['request_id'][0]
                task=client.get('/api/v1/verification/requests/'+rid).json()['verifier_task_id']
                route.fulfill(json={'completed':[{'task_id':task,'status':'failed_attempt'}]});return
            if url.path.endswith('/verify') and flags['fail_confirmation']:
                route.fulfill(status=503,json={'detail':'Confirmation unavailable fixture'});return
            result=client.request(req.method,url.path+('?' + url.query if url.query else ''),json=req.post_data_json if req.post_data else None)
            if url.path.startswith('/api/v1/verification/receipts/'):
                if flags['fail_receipt']:route.fulfill(status=503,json={'detail':'Receipt unavailable fixture'});return
                if flags['wrong_receipt']:
                    value=result.json();value['request_id']='foreign-request';route.fulfill(json=value);return
            route.fulfill(status=result.status_code,body=result.content,content_type='application/json')
        page.route('**/*',route);page.goto('http://127.0.0.1:8000/')
        for css in ('v5.css','v5-workflows.css','v5-home.css','v5-evolution.css','v5-polish.css','v5-workspace.css'):
            page.add_style_tag(content=(ROOT/'static'/css).read_text())
        page.add_script_tag(content=(ROOT/'static/v5-intel-import.js').read_text())
        page.add_script_tag(content=(ROOT/'static/v5-package-review.js').read_text())
        page.evaluate('''()=>{const call=async(url,body)=>{const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw new Error(value.detail);return value};window.review=window.createFieldworkPackageReview({request:url=>call(url),post:call,list:x=>Array.isArray(x)?x:[],esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),openVerification:()=>{window.receiptsOpened=true}})}''')
        page.evaluate("""()=>{const call=async(url,body)=>{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.detail);return data};window.importer=window.createFieldworkIntelImport({post:call,esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),onImported:()=>review.refresh()})}""")
        page.evaluate('(id)=>importer.update(id)',campaign_id)
        from tests.test_reviewed_intel_import import body as import_body
        payload=import_body()
        page.locator('#intelImportName').fill(payload['source_name'])
        page.locator('#intelImportLicense').fill(payload['license'])
        page.locator('#intelImportUrl').fill(payload['source_url'])
        page.locator('#intelImportFile').set_input_files({'name':'advisories.json','mimeType':'application/json','buffer':json.dumps(payload['records']).encode()})
        page.wait_for_function("document.querySelector('#intelImportJson').value.length>0")
        page.locator('#intelImportForm button').click()
        page.locator('#intelImportCommit').wait_for()
        page.set_viewport_size({'width':375,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.set_viewport_size({'width':1280,'height':1000})
        with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_records').fetchone()[0]==0
        page.locator('#intelImportLicense').fill('Changed operator declaration')
        assert page.locator('#intelImportCommit').count()==0
        page.locator('#intelImportForm button').click()
        page.locator('#intelImportAccepted').check()
        page.locator('#intelImportCommit').click()
        page.wait_for_function("document.querySelector('#intelImportStatus').textContent.includes('Import complete: inserted 1')")
        with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_records').fetchone()[0]==1
        baseline_writes=len(writes)
        page.evaluate('(id)=>review.update(id)',match['campaign_id'])
        page.locator('#packageFingerprintPreview').click()
        page.locator('#packageFingerprintCommit').wait_for()
        page.locator('#packageFingerprintCommit details summary').click()
        page.set_viewport_size({'width':375,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.set_viewport_size({'width':1280,'height':1000})
        assert 'example-pkg' in page.locator('#packageFingerprintCommit').inner_text()
        assert len(writes)==baseline_writes
        page.locator('#packageFingerprintCommit input').check()
        page.locator('#packageFingerprintCommit button').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('Package versions processed: 1')")
        match=client.get('/api/v1/intelligence/matches',params={'campaign_id':campaign_id}).json()['items'][0]
        assert len(writes)==baseline_writes+1 and writes[-1].endswith('/fingerprint')
        page.locator('[data-package-plan]').click()
        try:page.locator('#packageCommit').wait_for(timeout=5000)
        except Exception:
            print({'page_errors':errors,'visible':page.locator('#packageVerification').inner_text()},flush=True);raise
        assert len(writes)==baseline_writes+1
        page.locator('#packageCommit input').check();page.locator('#packageCommit button').click()
        page.locator('#packageExecute').wait_for()
        with final_core.connect() as db:assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0]==0
        flags['fail_tick']=True
        page.locator('#packageExecute').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('This request did not execute successfully')")
        assert page.locator('#packageResult').count()==0
        assert len([x for x in writes if x.endswith('/local/tick')])==1
        flags['fail_tick']=False
        page.locator('#packageExecute').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Package version applicability confirmed')")
        assert page.locator('#packageExecute').is_disabled()
        assert 'exploitability remain unproven' in page.locator('#packageResult').inner_text()
        page.locator('#packageReceipts').click();assert page.evaluate('window.receiptsOpened') is True
        flags['fail_confirmation']=True
        page.locator('#packageConfirm input').check()
        page.locator('#packageConfirm button').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('Confirmation unavailable fixture')")
        assert page.locator('#packageResult').count()==0
        flags['fail_confirmation']=False
        page.locator('#packageReadResult').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Package version applicability confirmed')")
        page.locator('#packageConfirm input').check()
        page.locator('#packageConfirm button').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('This package applicability result is recorded')")
        with final_core.connect() as db:
            assert db.execute('SELECT applicability FROM intel_matches WHERE id=?',(match['id'],)).fetchone()[0]=='verified'
        before_history=len(writes)
        page.evaluate("review.update('')")
        page.evaluate('(id)=>review.update(id)',match['campaign_id'])
        page.locator('[data-package-history]').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Package version applicability confirmed')")
        assert len(writes)==before_history
        assert page.locator('#packageConfirm').count()==0

        flags['wrong_receipt']=True
        page.locator('#packageReadResult').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('Receipt does not match this request')")
        assert page.locator('#packageResult').count()==0
        flags['wrong_receipt']=False;flags['fail_receipt']=True
        page.locator('#packageReadResult').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('Receipt unavailable fixture')")
        assert page.locator('#packageResult').count()==0
        flags['fail_receipt']=False
        page.locator('#packageReadResult').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Package version applicability confirmed')")

        with final_core.connect() as db:
            db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(match['rationale']['advisory_node_id'],))
        page.locator('#packageReadResult').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Applicability cannot currently be confirmed')")
        assert len([x for x in writes if x.endswith('/local/tick')])==2

        history_writes=len(writes)
        page.locator('#packageRefresh').click()
        page.wait_for_function("document.querySelector('#packageVerification').textContent.includes('historical review only')")
        assert page.locator('[data-package-plan]').is_disabled()
        page.locator('[data-package-history]').click()
        page.wait_for_function("document.querySelector('#packageResult')?.textContent.includes('Applicability cannot currently be confirmed')")
        assert len(writes)==history_writes

        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM verification_receipts_v5').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        page.evaluate("document.documentElement.lang='zh-CN';document.dispatchEvent(new Event('fieldwork:languagechange'))")
        assert '软件包适用性复验' in page.locator('#packageVerification').inner_text()
        before_layout=len(writes)
        saved_name=page.locator('#intelImportName').input_value()
        output=ROOT/'build/acceptance/package-source-layout';output.mkdir(parents=True,exist_ok=True)
        for language in ('en','zh-CN'):
            page.evaluate("lang=>{document.documentElement.lang=lang;document.dispatchEvent(new Event('fieldwork:languagechange'))}",language)
            assert page.locator('#intelImportName').input_value()==saved_name
            assert ('Import complete' if language=='en' else '导入完成') in page.locator('#intelImportStatus').inner_text()
            assert ('Execution completed' if language=='en' else '执行完成') in page.locator('#packageVerification').inner_text()
            assert ('Package applicability recorded' if language=='en' else '已记录包适用性') in page.locator('.package-match-row').inner_text()
            for theme in ('dark','light'):
                page.evaluate("theme=>document.documentElement.dataset.theme=theme",theme)
                for width in (1440,1024,680,375):
                    page.set_viewport_size({'width':width,'height':1000})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(language,theme,width)
                    if width in (1440,375):
                        page.evaluate('window.scrollTo(0,0)')
                        page.screenshot(path=str(output/f'{language}-{theme}-{width}-top.png'))
                        page.locator('#packageResult').scroll_into_view_if_needed()
                        page.screenshot(path=str(output/f'{language}-{theme}-{width}-result.png'))
        print({'panels':page.locator('article.panel').count(),'import_count':page.locator('#intelligenceImport').count(),'package_count':page.locator('#packageVerification').count(),'headings':page.locator('h2').all_text_contents()},flush=True)
        assert page.locator('#intelligenceImport').count()==1
        assert page.locator('#packageVerification').count()==1
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output/'viewport-top-375.png'))
        assert len(writes)==before_layout
        assert not errors,errors
        browser.close()
    print(json.dumps({'page_errors':errors,'writes':writes,'receipt_count':1,'canonical_findings':0,'scope':'real APIs; persisted tool fixture, actual isolated verifier, no actual scan/model'}))
