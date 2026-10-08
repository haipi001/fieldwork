"""Runtime configuration UI using real APIs, isolated DB, and local health fixture.

This proves configuration/routing/queue operations, not actual model research.
No cloud calls, background workers, or production data are used.
"""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app
import final_core
import runtime_secrets
from tests.test_final import create_ready


def run():
    counts={"health":0,"model":0}
    class HealthFixture(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path=="/v1/models"
            counts["health"]+=1
            self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
            self.wfile.write(b'{"data":[{"id":"ui-fixture-model"}]}')
        def do_POST(self):
            counts["model"]+=1
            self.send_response(503);self.end_headers()
        def log_message(self,*args): pass
    health=ThreadingHTTPServer(("127.0.0.1",0),HealthFixture)
    threading.Thread(target=health.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="fieldwork-runtime-ui-") as directory,ExitStack() as stack:
            root=Path(directory)
            for module,name,value in ((app,"DB",root/"ui.db"),(app,"DATA",root),
                                      (final_core,"DB",root/"ui.db"),(final_core,"LOCAL_DATA_ROOT",root)):
                stack.enter_context(patch.object(module,name,value))
            stack.enter_context(patch.object(runtime_secrets,"secret_root",lambda:root/"secrets"))
            stack.enter_context(patch.dict(os.environ,{"FIELDWORK_SESSION_TOKEN":secrets.token_urlsafe(48)}))
            app.prepare_database_upgrade(app.DB,root/"backups",ROOT)
            app.init_db();final_core.init_final_db();app.apply_v5_schema(app.DB);app.apply_v6_schema(app.DB);app.finalize_database_version(app.DB)
            client=TestClient(app.app,base_url="http://127.0.0.1:8000",headers={"X-Fieldwork-Session":os.environ["FIELDWORK_SESSION_TOKEN"]})
            project=create_ready(client,target="https://runtime-ui.example.test")
            from v6_eval_seeds import run_policy_seed
            with final_core.connect() as db:
                db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('ui-eval-run',project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],
                     'completed','report',1,None,None,None,None,final_core.utcnow()))
                eval_id=run_policy_seed(db,'ui-eval-run',root/'eval-artifacts')
                eval_path=Path(db.execute('SELECT a.uri FROM eval_runs_v6 e JOIN artifacts a ON a.id=e.artifact_id WHERE e.id=?',(eval_id,)).fetchone()[0])
                artifact_hash=db.execute('SELECT sha256 FROM artifacts WHERE uri=?',(str(eval_path),)).fetchone()[0]
                for index in range(51):
                    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                        (f'ui-page-material-{index:03d}','ui-eval-run','pagination_fixture',str(eval_path),artifact_hash,
                         'application/json',1,final_core.utcnow()))
                db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('ui-policy-run',project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],
                     'running','target',0,None,None,None,None,final_core.utcnow()))
            from v6_http_gateway import authorize_native_browser_read
            try:
                authorize_native_browser_read('ui-policy-run',project['id'],'https://outside-ui.example.test/')
            except ValueError:
                pass
            import agent_audit
            from tests.test_agent_audit import create, upload, analyze
            agent_audit.init_agent_audit_db()
            audit_fixture=agent_audit.demo_fixture()
            audit_id=create(client,audit_fixture)
            for body in audit_fixture['imports']:
                upload(client,audit_id,body)
            analyze(client,audit_id)
            with final_core.connect() as db:
                incident=db.execute('SELECT i.candidate_id,c.run_id,e.artifact_id,a.uri FROM agent_incidents i '
                    'JOIN candidate_findings c ON c.id=i.candidate_id JOIN agent_events e ON e.id=i.event_id '
                    'JOIN artifacts a ON a.id=e.artifact_id WHERE i.audit_id=? LIMIT 1',(audit_id,)).fetchone()
            incident_route=f"/api/v1/v6/incidents/{incident['candidate_id']}/response"
            assert client.post(incident_route,json=dict(expected_state='DETECTED',state='TRIAGED',artifact_id=incident['artifact_id'])).status_code==200
            campaign=client.post(f"/api/v1/engagements/{project['id']}/campaigns",json={"name":"Runtime UI fixture","objective":"Inspect configuration and scheduling"}).json()
            assert client.post("/api/v1/research/nodes",json={"campaign_id":campaign["id"],"node_type":"observation","title":"Explicit scheduling fixture"}).status_code==201
            writes,errors=[],[]
            flags={"fail_config":False,"fail_summary":False}
            def route_request(route):
                request=route.request;parsed=urlparse(request.url);path=parsed.path
                assert parsed.hostname=="127.0.0.1"
                if path=="/v5":route.fulfill(path=str(ROOT/"templates/v5.html"),content_type="text/html")
                elif path.startswith("/static/"):route.fulfill(path=str(ROOT/path.lstrip("/")))
                elif (path in {"/api/v1/engagements", "/api/v1/v6/runtime-summary", "/api/v1/v6/eval-runs", "/api/v1/v6/policy-decisions"} or path.startswith("/api/v1/orchestration/")
                      or path.startswith('/api/v1/v6/runs/') and path.endswith('/evidence-lineage')
                      or path.startswith('/api/v1/v6/incidents/') and path.endswith('/response')
                      or path==f"/api/v1/engagements/{project['id']}/campaigns"
                      or path.startswith("/api/v1/continuous-research/")
                      or path.startswith("/api/v1/runtime/") and path!="/api/v1/runtime/readiness"):
                    if flags["fail_config"] and path=="/api/v1/runtime/config":
                        route.fulfill(status=503,json={"detail":"injected configuration read failure"});return
                    if flags["fail_summary"] and path=="/api/v1/v6/runtime-summary":
                        route.fulfill(status=503,json={"detail":"injected summary failure"});return
                    if request.method!="GET":writes.append(path)
                    response=client.request(request.method,path+("?"+parsed.query if parsed.query else ""),content=request.post_data,headers={"Content-Type":"application/json"})
                    route.fulfill(status=response.status_code,body=response.content,content_type="application/json")
                elif path=="/api/v1/findings":route.fulfill(json={"verified":[],"candidates":[]})
                elif path in {"/api/v1/task-center","/api/v1/capabilities","/api/v1/agent-audit/audits"}:route.fulfill(json=[])
                else:
                    assert request.method=="GET","unexpected integration write"
                    route.fulfill(status=503,json={"detail":"outside runtime fixture"})
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.getenv("FIELDWORK_CHROME_PATH","/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
                page=browser.new_page(viewport={"width":1440,"height":1000})
                page.on("pageerror",lambda error:errors.append(str(error)))
                page.on("dialog",lambda dialog:dialog.accept())
                page.route("**/*",route_request)
                page.goto("http://127.0.0.1:8000/v5#runtime")
                page.wait_for_selector('[data-summary-total="tasks"]')
                assert page.locator('[data-summary-total="tasks"]').inner_text() == str(client.get('/api/v1/v6/runtime-summary').json()['tasks']['total'])
                page.wait_for_function("document.querySelector('#rcCampaign').value !== ''")
                assert not writes and counts=={"health":0,"model":0}
                page.locator('#rcDecisions').locator('..').locator('summary').click()
                page.wait_for_selector('[data-policy-decision="deny"]')
                assert 'outside-ui.example.test' in page.locator('#rcDecisions').inner_text()
                page.locator('#rcEvals').locator('..').locator('summary').click()
                page.wait_for_selector('[data-eval-status="passed"]')
                page.locator('#rcEvidence').locator('..').locator('summary').first.click()
                page.locator('#rcEvidenceRun').fill('ui-eval-run')
                page.locator('#rcEvidenceRead').click()
                page.wait_for_selector('[data-evidence-integrity="intact"]')
                assert page.locator('[data-evidence-integrity]').count()==50
                assert page.locator('#rcEvidencePrevious').is_disabled()
                page.locator('#rcEvidenceRun').fill('missing-run')
                page.locator('#rcEvidenceNext').click()
                page.wait_for_function("document.querySelector('#rcEvidence').textContent.includes('51–52')")
                assert page.locator('[data-evidence-integrity]').count()==2
                assert page.locator('#rcEvidenceNext').is_disabled()
                assert 'ui-eval-run' in page.locator('#rcEvidence').inner_text()
                page.locator('#rcEvidencePrevious').click()
                page.wait_for_function("document.querySelector('#rcEvidence').textContent.includes('1–50')")
                assert page.locator('[data-evidence-integrity]').count()==50
                page.locator('#rcEvidenceRun').fill('ui-eval-run')
                eval_path.write_text('{}')
                page.locator('#rcRefresh').click()
                page.wait_for_selector('[data-eval-status="invalid"]')
                page.locator('#rcEvidenceRead').click()
                page.wait_for_selector('[data-evidence-integrity="missing_or_changed"]')
                page.locator('#rcEvidenceRun').fill('missing-run')
                page.locator('#rcEvidenceRead').click()
                page.wait_for_function("document.querySelector('#rcEvidence').textContent.includes('404')")
                assert page.locator('[data-evidence-integrity]').count()==0
                page.locator('#rcEvidenceRun').fill('ui-eval-run')
                page.locator('#rcEvidenceRead').click()
                page.wait_for_selector('[data-evidence-integrity="missing_or_changed"]')
                page.locator('#rcIncident').locator('..').locator('summary').first.click()
                page.locator('#rcIncidentId').fill(incident['candidate_id'])
                page.locator('#rcIncidentRead').click()
                page.wait_for_selector('[data-incident-state="TRIAGED"]')
                page.wait_for_selector('[data-incident-integrity="intact"]')
                Path(incident['uri']).write_text('{}')
                page.locator('#rcIncidentRead').click()
                page.wait_for_selector('[data-incident-integrity="missing_or_changed"]')
                page.locator('#rcIncidentId').fill('missing-incident')
                page.locator('#rcIncidentRead').click()
                page.wait_for_function("document.querySelector('#rcIncident').textContent.includes('404')")
                assert page.locator('[data-incident-state]').count()==0
                assert page.locator('[data-incident-integrity]').count()==0
                page.locator('#rcIncidentId').fill(incident['candidate_id'])
                page.locator('#rcIncidentRead').click()
                page.wait_for_selector('[data-incident-integrity="missing_or_changed"]')
                flags['fail_summary']=True;page.locator('#rcRefresh').click()
                page.wait_for_function("document.querySelector('#rcSummary').textContent.includes('503')")
                assert page.locator('[data-summary-total]').count()==0
                flags['fail_summary']=False;page.locator('#rcRefresh').click()
                page.wait_for_selector('[data-summary-total="tasks"]')
                page.locator("#rcProviderForm").locator("..").locator("summary").click()
                page.locator("#rcProviderName").fill("Local UI fixture")
                page.locator("#rcModel").fill("ui-fixture-model")
                page.locator("#rcBase").fill(f"http://127.0.0.1:{health.server_port}/v1")
                page.locator("#rcKey").fill("fixture-key-no-real-credential")
                page.locator("#rcIndependent").check()
                page.locator("#rcProviderAccepted").check()
                page.locator("#rcProviderSave").click()
                page.wait_for_function("document.querySelectorAll('[data-rc-health]').length === 1")
                assert page.locator("#rcKey").input_value()==""
                assert counts["health"]==0
                page.locator("[data-rc-health]").click()
                page.wait_for_function("document.querySelector('#rcProviderActions').textContent.includes('healthy')")
                assert counts=={"health":1,"model":0}
                page.locator("#rcProfileForm").locator("..").locator("summary").click()
                page.locator("#rcProfileName").fill("Frozen UI offline profile")
                page.locator("#rcMode").select_option("offline")
                page.locator("#rcLocalProviders input").check()
                page.locator("#rcIndependentProviders input").check()
                page.locator("#rcCallConcurrency").fill("2")
                page.locator("#rcHourlyTokens").fill("64000")
                page.locator("#rcCallRuntime").fill("20000")
                page.locator("#rcProfileAccepted").check()
                page.locator("#rcProfileSave").click()
                page.wait_for_function("document.querySelector('#rcRouteProfile').value.startsWith('profile-')")
                saved=client.get("/api/v1/runtime/config").json()
                assert len(saved["profiles"])==1 and saved["profiles"][0]["config"]["provider_config_hashes"]
                assert saved["profiles"][0]["config"]["max_concurrent_calls"]==2
                assert saved["profiles"][0]["config"]["max_tokens_per_hour"]==64000
                assert saved["profiles"][0]["config"]["max_runtime_ms_per_call"]==20000
                assert page.locator("#rcCalls").inner_text()
                page.locator("#rcRouteForm").locator("..").locator("summary").click()
                page.locator("#rcSensitivity").select_option("secret")
                page.locator("#rcRouteIndependent").check()
                page.locator("#rcRouteCheck").click()
                page.wait_for_function("document.querySelector('#rcRouteResult').textContent.includes('selected')")
                result=json.loads(page.locator("#rcRouteResult").inner_text())
                assert result["mode"]=="offline" and result["route"]=="independent" and result["model"]=="ui-fixture-model"
                assert client.get("/api/v1/runtime/usage").json()["total"]["calls"]==0
                assert counts["model"]==0
                page.locator("#rcPolicyForm").locator("..").locator("summary").click()
                page.locator("#rcEnabled").check()
                page.locator("#rcPolicyAccepted").check()
                page.locator("#rcPolicySave").click()
                page.wait_for_function("document.querySelector('#rcPolicyState').textContent.includes('配置版本: 1')")
                page.locator("#rcTick").click()
                page.wait_for_function("document.querySelector('#rcTickResult').textContent.includes('checkpointed')")
                assert len(client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}").json()["items"])==1
                page.locator("#rcTickTasks").fill("3")
                page.locator("#rcPolicyAccepted").check()
                page.locator("#rcPolicySave").click()
                page.wait_for_function("document.querySelector('#rcPolicyState').textContent.includes('配置版本: 2')")
                assert client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}").json()["items"][0]["status"]=="cancelled"
                page.locator('#rcRefresh').click()
                page.wait_for_function("document.querySelector('[data-summary-total=tasks]').textContent==='2'")
                page.locator("#rcProviderName").fill("Unsaved input stays intact")
                page.locator("#languageToggle").click()
                assert page.locator("#rcProviderName").input_value()=="Unsaved input stays intact"
                assert "V5 model configuration" in page.locator("#runtimeControl h2").inner_text()
                flags["fail_config"]=True;page.locator("#rcRefresh").click()
                page.wait_for_function("document.querySelector('#rcMessage').textContent.includes('503')")
                flags["fail_config"]=False;page.locator("#rcRefresh").click()
                page.wait_for_function("document.querySelector('#rcPolicyState').textContent.includes('Configuration revision: 2')")
                page.reload()
                page.wait_for_function("document.querySelectorAll('[data-rc-health]').length===1")
                assert page.locator("#rcKey").input_value()==""
                assert "Frozen UI offline profile" in page.locator("#rcRouteProfile").text_content()
                assert "fixture-key-no-real-credential" not in page.evaluate("JSON.stringify(localStorage)")
                page.locator('#rcIncident').locator('..').locator('summary').first.click()
                page.locator('#rcIncidentId').fill(incident['candidate_id'])
                page.locator('#rcIncidentRead').click()
                page.wait_for_selector('[data-incident-integrity="missing_or_changed"]')
                page.locator('#rcEvidence').locator('..').locator('summary').first.click()
                page.locator('#rcEvidenceRun').fill('ui-eval-run')
                page.locator('#rcEvidenceRead').click()
                page.wait_for_selector('[data-evidence-integrity="missing_or_changed"]')
                output=ROOT/"build/acceptance/v5-runtime-ui";output.mkdir(parents=True,exist_ok=True)
                for language in ("en","zh-CN"):
                    page.set_viewport_size({"width":1440,"height":1000})
                    if page.locator("html").get_attribute("lang")!=language:page.locator("#languageToggle").click()
                    for theme in ("dark","light"):
                        page.set_viewport_size({"width":1440,"height":1000})
                        if page.locator("html").get_attribute("data-theme")!=theme:page.locator("#themeToggle").click()
                        for width in (1440,680):
                            page.set_viewport_size({"width":width,"height":1000})
                            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"),(language,theme,width)
                            page.screenshot(path=str(output/f"{language}-{theme}-{width}.png"))
                assert not errors,errors
                browser.close()
            print(json.dumps({"providers":1,"profiles":1,"policy_revisions":2,"health_requests":counts["health"],"model_requests":counts["model"],"page_errors":errors,"scope":"real in-process APIs and local health fixture; no model research"}))
    finally:
        health.shutdown();health.server_close()


if __name__=="__main__":run()
