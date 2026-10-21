"""Chrome UI against real orchestration APIs and an isolated temporary database.

The Run is an explicit paused fixture; this checks team configuration and queue
persistence and invalid-output billing through a local HTTP protocol fixture.
This is not actual model inference or target discovery. Unrelated integrations
return unavailable.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app
import final_core
import v5_research_worker as research
from tests.test_final import create_ready


def run():
    with tempfile.TemporaryDirectory(prefix="fieldwork-team-ui-") as directory, ExitStack() as stack:
        root = Path(directory).resolve()
        for module, name, value in ((app, "DB", root / "ui.db"), (app, "DATA", root),
                                    (final_core, "DB", root / "ui.db"), (final_core, "LOCAL_DATA_ROOT", root)):
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch.dict(os.environ, {"FIELDWORK_SESSION_TOKEN": secrets.token_urlsafe(48)}))
        app.prepare_database_upgrade(root / "ui.db", root / "backups", ROOT)
        app.init_db()
        final_core.init_final_db()
        app.init_guided_db()
        app.apply_v5_schema(root / "ui.db")
        app.apply_v6_schema(root / "ui.db")
        app.finalize_database_version(root / "ui.db")
        client = TestClient(app.app, base_url="http://127.0.0.1:8000",
                            headers={"X-Fieldwork-Session": os.environ["FIELDWORK_SESSION_TOKEN"]})
        engagement = create_ready(client, target="https://team-ui.example.test")
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "UI acceptance fixture", "objective": "Investigate the selected evidence",
        }).json()
        with final_core.connect() as db:
            db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "ui-real-run", engagement["id"], "traditional", engagement["current_scope_snapshot_id"],
                engagement["current_policy_id"], "paused", "target", 0, None, None, None, None, final_core.utcnow(),
            ))
        assert client.put("/api/v1/runners/ui-runner", json={
            "id": "ui-runner", "name": "Registered fixture runner", "kind": "research-worker",
            "max_concurrency": 2,
        }).status_code == 200
        writes, errors, delayed = [], [], []
        policy_reads=[]
        flags = {"hold_preview": False, "fail_groups": False,"foreign_run":False}

        def route_request(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path
            if request.method=='GET' and path.endswith('/resume-policy'):policy_reads.append(path)
            assert parsed.hostname == "127.0.0.1", "unexpected target request"
            if path == "/v5":
                route.fulfill(body=re.sub(r"\{\{ asset_url\('([^']+)'\) \}\}", r"/static/\1", (ROOT / "templates/v5.html").read_text()), content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(ROOT / path.lstrip("/")))
            elif (path in {"/api/v1/engagements", "/api/v1/runs", "/api/v1/runtime/config"}
                  or path.startswith("/api/v1/runs/")
                  or path.startswith("/api/v1/orchestration/")
                  or path.startswith("/api/v1/workers/research/")
                  or path == f"/api/v1/engagements/{engagement['id']}/campaigns"):
                if request.method != "GET":
                    writes.append(path)
                if flags["fail_groups"] and path == "/api/v1/orchestration/groups":
                    route.fulfill(status=503, json={"detail": "injected read failure"})
                    return
                if flags['foreign_run'] and path=='/api/v1/runs/ui-real-run':
                    route.fulfill(json={'id':'ui-real-run','engagement_id':'foreign-project'})
                    return
                response = client.request(request.method, path + ("?" + parsed.query if parsed.query else ""),
                                          content=request.post_data,
                                          headers={"Content-Type": "application/json"})
                if flags.get('hold_resume_save') and request.method=='POST' and path.endswith('/resume-policy'):
                    delayed.append((route,response));return
                if flags.get('legacy_resume') and request.method=='GET' and path.endswith('/resume-policy'):
                    legacy=response.json();legacy.pop('task_progress',None);legacy['state']='completed'
                    route.fulfill(status=response.status_code,json=legacy);return
                if flags["hold_preview"] and path.endswith("/team/preview"):
                    delayed.append((route, response))
                    return
                route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            elif path == "/api/v1/findings":
                route.fulfill(json={"verified": [], "candidates": []})
            elif path in {"/api/v1/task-center", "/api/v1/agent-audit/audits", "/api/v1/capabilities"}:
                route.fulfill(json=[])
            else:
                assert request.method == "GET", "unexpected integration write"
                route.fulfill(status=503, json={"detail": "Outside isolated UI acceptance"})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=os.getenv(
                "FIELDWORK_CHROME_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.route("**/*", route_request)
            page.goto("http://127.0.0.1:8000/v5#agents")
            page.wait_for_function("document.querySelector('#teamCreate') && !document.querySelector('#teamCreate').disabled")
            assert not writes
            assert "尚无研究组" in page.locator("#agentsContent").inner_text()
            for count in (1, 2, 8):
                page.locator("#teamCreate").click()
                page.locator("#teamAdvanced summary").click()
                page.locator("#teamResearchers").fill(str(count))
                page.locator("#teamConcurrency").fill(str(min(count, 2)))
                page.wait_for_function("!document.querySelector('#teamCommit').disabled")
                assert f"{count} 个逻辑任务" in page.locator("#teamPreviewResult").inner_text()
                page.locator("#teamAccepted").check()
                page.locator("#teamCommit").click()
                page.wait_for_function("!document.querySelector('#teamDialog').open")
                page.wait_for_function(f"document.querySelectorAll('#agentsContent tbody tr').length === {count}")
            assert len(client.get(f"/api/v1/orchestration/groups?campaign_id={campaign['id']}").json()["items"]) == 3
            assert len(client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}").json()["items"]) == 11

            # A response from an older edited plan must never re-enable commit.
            flags["hold_preview"] = True
            page.locator("#teamCreate").click()
            page.wait_for_function("document.querySelector('#teamPreview').disabled")
            page.locator("#teamAdvanced summary").click()
            page.locator("#teamResearchers").fill("2")
            assert delayed
            route, response = delayed.pop()
            route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
            flags["hold_preview"] = False
            page.wait_for_function("!document.querySelector('#teamPreview').disabled")
            assert page.locator("#teamCommit").is_disabled()
            assert page.locator("#teamPreviewResult").inner_text() == ""
            page.locator("#teamPreview").click()
            page.wait_for_function("!document.querySelector('#teamCommit').disabled")
            page.locator("#teamResearchers").fill("3")
            page.evaluate("document.querySelector('#languageToggle').click()")
            assert page.locator("#teamDialogTitle").inner_text() == "Configure research team"
            assert page.locator("#teamResearchers").input_value() == "3"
            assert page.locator("#teamCommit").is_disabled()
            assert page.locator("#teamPreviewResult").inner_text() == ""
            page.locator("#teamCancel").click()

            # Error, refresh, pause/resume/cancel and persisted reload.
            flags["fail_groups"] = True
            page.locator("#teamRefresh").click()
            page.wait_for_function("!!document.querySelector('#agentsContent [role=alert]')")
            flags["fail_groups"] = False
            page.locator("#teamRefresh").click()
            page.wait_for_function("document.querySelectorAll('.team-group').length === 3")
            page.locator('[data-team-action="pause"]').first.click()
            page.wait_for_function("!!document.querySelector('[data-team-action=resume]')")
            page.locator('[data-team-action="resume"]').click()
            page.wait_for_function("document.querySelectorAll('[data-team-action=pause]').length === 3")
            page.locator('[data-team-action="cancel"]').first.click()
            page.wait_for_function("document.querySelectorAll('[data-team-action=cancel]').length === 2")
            page.reload()
            page.wait_for_function("document.querySelectorAll('.team-group').length === 3")
            assert "11" in page.locator(".team-summary").inner_text()
            # Put all group tasks beyond the unfiltered first 100 records.
            with final_core.connect() as db:
                wanted=db.execute('SELECT group_id FROM agent_tasks WHERE group_id IS NOT NULL GROUP BY group_id HAVING count(*)=8').fetchone()[0]
                wanted_ids=[row[0] for row in db.execute('SELECT id FROM agent_tasks WHERE group_id=?',(wanted,))]
                template=dict(db.execute('SELECT * FROM agent_tasks LIMIT 1').fetchone())
                for index in range(110):
                    noise=template|{'id':f'ui-page-noise-{index:03d}','idempotency_key':f'ui-page-key-{index:03d}','group_id':None,'priority':99,'objective':'Pagination noise fixture'}
                    db.execute('INSERT INTO agent_tasks('+','.join(noise)+') VALUES('+','.join('?' for _ in noise)+')',tuple(noise.values()))
            page.locator('#teamRefresh').click()
            page.wait_for_function("document.querySelectorAll('#agentsContent tbody tr').length===100")
            assert not any(identifier in page.locator('#agentsContent tbody').inner_text() for identifier in wanted_ids)
            page_writes=len(writes)
            page.locator(f'[data-team-select="{wanted}"]').click()
            page.wait_for_function("document.querySelectorAll('#agentsContent tbody tr').length===8")
            assert all(identifier in page.locator('#agentsContent tbody').inner_text() for identifier in wanted_ids)
            assert 'ui-page-noise-' not in page.locator('#agentsContent tbody').inner_text()
            page.locator('#teamAllTasks').click()
            page.wait_for_function("document.querySelectorAll('#agentsContent tbody tr').length===100")
            page.locator('#teamMoreTasks').click()
            page.wait_for_function("document.querySelectorAll('#agentsContent tbody tr').length===121")
            assert len(writes)==page_writes
            with final_core.connect() as db:db.execute("DELETE FROM agent_tasks WHERE id LIKE 'ui-page-noise-%'")
            page.locator('#teamRefresh').click()
            page.wait_for_function("document.querySelectorAll('#agentsContent tbody tr').length===11")
            page.locator('[data-view="runners"]').click()
            assert "Registered fixture runner" in page.locator("#runnersContent").inner_text()
            page.locator('[data-view="agents"]').click()
            page.wait_for_function("document.querySelectorAll('.team-group').length === 3")
            model_hits = []
            class BadOutput(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers['Content-Length']))
                    model_hits.append(self.path)
                    raw = json.dumps({'choices':[{'message':{'content':'{"summary":"fixture-private-output'}}],
                        'usage':{'prompt_tokens':17,'completion_tokens':12}}).encode()
                    self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers()
                    self.wfile.write(raw)
                def log_message(self,*_):
                    pass
            model_server = ThreadingHTTPServer(('127.0.0.1',0),BadOutput)
            serving = threading.Thread(target=model_server.serve_forever,daemon=True); serving.start()
            stack.callback(serving.join,2)
            stack.callback(model_server.server_close)
            stack.callback(model_server.shutdown)
            stack.callback(research.stop_workers)
            with final_core.connect() as db:
                now=final_core.utcnow()
                db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
                    'bad-ui-model','llama_cpp','Bad output HTTP fixture',f'http://127.0.0.1:{model_server.server_port}',
                    'fixture',None,1,'{"location":"local"}','healthy',now,now,now))
                live_group=db.execute("SELECT g.id FROM research_groups g JOIN agent_tasks t ON t.group_id=g.id "
                    "WHERE g.status='active' GROUP BY g.id ORDER BY COUNT(*) LIMIT 1").fetchone()[0]
                live_count=db.execute('SELECT COUNT(*) FROM agent_tasks WHERE group_id=?',(live_group,)).fetchone()[0]
            page.locator(f'[data-team-start="{live_group}"]').click()
            research._jobs[live_group].join(timeout=5)
            assert not research._jobs[live_group].is_alive()
            page.locator('#teamRefresh').click()
            fixture_tasks = client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}").json()['items']
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            # The default Profile permits one model call at a time; a second task
            # may be budget-blocked before sending any protocol request.
            executed = [item for item in fixture_tasks if item['group_id']==live_group]
            settled = [item for item in executed if item.get('error',{}).get('code')=='model_output_invalid_usage_settled']
            blocked = [item for item in executed if item.get('error',{}).get('code')=='model_budget_blocked']
            assert len(executed)==live_count and len(settled)+len(blocked)==live_count
            assert all(item['status']=='paused' for item in executed)
            assert len(model_hits)==len(settled) and settled
            assert all(item['error']['model_output_reason']=='invalid_json' for item in settled)
            assert 'Output is not complete valid JSON' in page.locator('#agentsContent').inner_text()
            assert 'Output bytes:' in page.locator('#agentsContent').inner_text()
            assert 'Unterminated string' in page.locator('#agentsContent').inner_text()
            assert 'No automatic retry.' in page.locator('#agentsContent').inner_text()
            protocol_count=len(model_hits)
            assert '17 / 12' in page.locator('#agentsContent').inner_text()
            assert 'fixture-private-output' not in page.locator('#agentsContent').inner_text()
            page.reload()
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            assert len(model_hits)==protocol_count
            # Explicit saved-history fixture for the read-only review UI; no model success is inferred.
            from v5_graph import _bridge_node
            import v5_workers
            draft_task=executed[0]['id']
            with final_core.connect() as db:
                row=db.execute('SELECT * FROM agent_tasks WHERE id=?',(draft_task,)).fetchone()
                import hashlib
                source_file=root/'candidate-source.json';source_file.write_bytes(b'{"recorded":"fixture public source"}')
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',('candidate-source-artifact',row['run_id'],'fixture',str(source_file),hashlib.sha256(source_file.read_bytes()).hexdigest(),'application/json',1,final_core.utcnow()))
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',('candidate-source-observation',row['run_id'],engagement['id'],'traditional','authorization','https://team-ui.example.test/object','Recorded review input',.5,'fixture','candidate-source-artifact',final_core.utcnow()))
                source,_=_bridge_node(db,campaign['id'],source_type='observation',source_ref='candidate-source-observation',node_type='observation',title='Recorded review input',body='https://team-ui.example.test/object',status='recorded',run_id=row['run_id'],attributes={'mode':'traditional','observation_type':'authorization','source_capability':'fixture','artifact_ref':'candidate-source-artifact'})
                source_row=db.execute('SELECT * FROM research_nodes WHERE id=?',(source,)).fetchone()
                claim,_=_bridge_node(db,campaign['id'],source_type='team_research',source_ref=f'{draft_task}:claim:0',node_type='claim',title='Saved draft requires independent verification',body='A review hypothesis, not a finding',status='draft',run_id=row['run_id'],attributes={'task_id':draft_task,'evidence_ids':[source],'counterevidence_ids':[]})
                capsule=json.loads(row['context_capsule_json']);capsule['research_input_hashes']={source:v5_workers._node_digest(source_row)}
                db.execute("UPDATE agent_tasks SET status='succeeded',context_capsule_json=?,result_json=? WHERE id=?",(json.dumps(capsule),json.dumps({'status':'research_draft','summary':'Saved historical draft fixture','claim_ids':[claim],'open_question_ids':[]}),draft_task))
            page.locator('#teamRefresh').click()
            page.locator(f'[data-team-draft="{draft_task}"]').click()
            page.wait_for_function("document.querySelector('#teamDraftReview')?.textContent.includes('Graph inputs currently match')")
            assert 'Registered sources and file hashes checked' in page.locator('#teamDraftReview').inner_text()
            assert 'File hash checked' in page.locator('#teamDraftReview').inner_text()
            assert 'Not independently verified' in page.locator('#teamDraftReview').inner_text()
            assert 'Recorded review input' in page.locator('#teamDraftReview').inner_text()
            page.locator('#teamDraftReview details').first.locator('summary').click()
            page.get_by_role('button',name='Review as an unverified candidate',exact=True).click()
            handoff=page.get_by_role('dialog',name='Review unverified candidate',exact=True)
            handoff.get_by_role('button',name='Save unverified candidate',exact=True).wait_for()
            assert handoff.get_by_role('button',name='Save unverified candidate',exact=True).is_disabled()
            original_source=source_file.read_bytes();source_file.write_bytes(b'changed after candidate preview')
            handoff.locator('[data-authorized]').check();handoff.get_by_role('button',name='Save unverified candidate',exact=True).click()
            handoff.get_by_text('Draft graph or registered sources are not current and verified',exact=True).wait_for()
            assert handoff.get_by_role('button',name='Save unverified candidate',exact=True).is_disabled()
            with final_core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
            handoff.get_by_role('button',name='Cancel',exact=True).click();source_file.write_bytes(original_source)
            page.get_by_role('button',name='Review as an unverified candidate',exact=True).click()
            handoff.get_by_role('button',name='Save unverified candidate',exact=True).wait_for()
            handoff.locator('[data-authorized]').check();handoff.get_by_role('button',name='Save unverified candidate',exact=True).click()
            page.get_by_role('heading',name='Unverified candidate saved',exact=True).wait_for()
            page.get_by_role('dialog',name='Unverified candidate saved',exact=True).get_by_role('button',name='Close',exact=True).click()
            with final_core.connect() as db:
                assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==1
                assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
            assert len(model_hits)==protocol_count
            with final_core.connect() as db:
                second,_=_bridge_node(db,campaign['id'],source_type='team_research',source_ref=f'{draft_task}:claim:1',node_type='claim',title='Second saved draft for atomic batch',body='A second hypothesis, not a finding',status='draft',run_id=row['run_id'],attributes={'task_id':draft_task,'evidence_ids':[source],'counterevidence_ids':[]})
                result=json.loads(db.execute('SELECT result_json FROM agent_tasks WHERE id=?',(draft_task,)).fetchone()[0]);result['claim_ids'].append(second)
                db.execute('UPDATE agent_tasks SET result_json=? WHERE id=?',(json.dumps(result),draft_task))
            page.locator('#teamRefresh').click()
            page.locator(f'[data-team-handoff="{live_group}"]').click()
            batch=page.get_by_role('dialog',name='Review team drafts as a batch',exact=True)
            batch.locator('[data-batch-pick]').first.wait_for()
            assert batch.locator('[data-batch-pick]').count()==2
            prepare_calls=len(model_hits)
            batch.locator('[data-batch-prepare]').click()
            batch.locator('[data-batch-authorized]:enabled').wait_for()
            assert batch.locator('[data-batch-pick]:checked').count()==1
            assert 'Candidate already saved' in batch.locator('[data-prepared-note]').inner_text()
            assert batch.locator('[data-batch-save]').is_disabled()
            assert len(model_hits)==prepare_calls
            batch.locator('[data-batch-pick]').nth(0).check();batch.locator('[data-batch-pick]').nth(1).check()
            batch.locator('[data-batch-preview]').click()
            batch.locator('[data-batch-authorized]:enabled').wait_for()
            batch.locator('[data-batch-target]').first.select_option('https://team-ui.example.test/object')
            assert batch.locator('[data-batch-save]').is_disabled() and batch.locator('[data-batch-authorized]').is_disabled()
            batch.locator('[data-batch-preview]').click();batch.locator('[data-batch-authorized]:enabled').wait_for()
            page.set_viewport_size({'width':430,'height':900})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.set_viewport_size({'width':1440,'height':1000})
            source_file.write_bytes(b'changed after batch preview')
            batch.locator('[data-batch-authorized]').check();batch.locator('[data-batch-save]').click()
            batch.get_by_text('Draft graph or registered sources are not current and verified',exact=True).wait_for()
            assert batch.locator('[data-batch-save]').is_disabled()
            with final_core.connect() as db:assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==1
            source_file.write_bytes(original_source)
            batch.locator('[data-batch-preview]').click();batch.locator('[data-batch-authorized]:enabled').wait_for()
            batch.locator('[data-batch-authorized]').check();batch.locator('[data-batch-save]').click()
            page.get_by_role('heading',name='Unverified candidate batch saved',exact=True).wait_for()
            batch=page.get_by_role('dialog',name='Unverified candidate batch saved',exact=True)
            assert 'ui-real-run' in batch.inner_text() and 'Existing' in batch.inner_text() and 'Created' in batch.inner_text()
            with final_core.connect() as db:
                assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==2
                assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
                expected_ids=[row[0] for row in db.execute('SELECT id FROM candidate_findings ORDER BY id')]
                noise=dict(db.execute('SELECT * FROM candidate_findings LIMIT 1').fetchone())
                noise.update(id='unrelated-earlier-candidate',title='Unrelated earlier candidate must not replace selection')
                db.execute('INSERT INTO candidate_findings('+','.join(noise)+') VALUES('+','.join('?' for _ in noise)+')',tuple(noise.values()))
            before_follow=len(writes)
            batch.get_by_role('button',name='Review same-run candidates',exact=True).click()
            targeted=page.get_by_role('dialog',name='Review independent replay batch',exact=True)
            targeted.locator('[data-batch-authorized]').wait_for()
            assert all(candidate_id in targeted.inner_text() for candidate_id in expected_ids)
            assert 'Unrelated earlier candidate' not in targeted.inner_text()
            assert len(writes)==before_follow and len(model_hits)==protocol_count
            targeted.locator('[data-close]').click()
            assert page.locator('[data-workflow-continue]').is_disabled()
            assert page.locator('[data-workflow-focused]').count()==1
            page.set_viewport_size({'width':430,'height':900})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('[data-workflow-clear-focus]').click()
            page.wait_for_function("document.querySelector('#candidateWorkflow')?.textContent.includes('Loaded 3 records')")
            assert page.locator('[data-workflow-focused]').count()==0
            assert page.locator('[data-workflow-continue]').is_enabled()
            page.get_by_role('button',name='Agents',exact=True).click()
            page.locator('#teamRefresh').click()
            assert len(model_hits)==protocol_count
            with final_core.connect() as db:db.execute("UPDATE research_nodes SET body='changed' WHERE id=?",(source,))
            page.locator(f'[data-team-draft="{draft_task}"]').click()
            page.wait_for_function("document.querySelector('#teamDraftReview')?.textContent.includes('Graph inputs are missing or changed')")
            before_handoff=len(writes)
            flags['foreign_run']=True
            page.locator('#teamDraftReview').get_by_role('button',name="Review this run's replay candidates and blockers",exact=True).click()
            page.get_by_text('Run does not belong to the current project; select again',exact=True).wait_for()
            assert page.url.endswith('#agents') and len(writes)==before_handoff
            flags['foreign_run']=False
            page.locator('#teamDraftReview').get_by_role('button',name="Review this run's replay candidates and blockers",exact=True).click()
            page.wait_for_function("document.querySelector('#workflowRun')?.value==='ui-real-run' && document.querySelector('#candidateWorkflow')?.textContent.includes('Loaded 3 records')")
            assert len(writes)==before_handoff and len(model_hits)==protocol_count
            page.evaluate('(project)=>window.FieldworkCandidateWorkflow.update([],project)',engagement['id'])
            assert page.locator('#workflowRun').input_value()=='ui-real-run'
            # Observe the synchronous project-clear operation in one browser turn;
            # the app's unrelated refresh can subsequently restore its real project.
            assert page.evaluate('()=>{window.FieldworkCandidateWorkflow.update([],"different-project");return document.querySelector("#workflowRun").value}')==''
            page.get_by_role('button',name='Agents',exact=True).click()
            page.locator('#teamRefresh').click()
            assert len(model_hits)==protocol_count
            for reason,caption in [('output_truncated','Generation reached its output limit; response truncated'),('schema_mismatch','JSON fields or types do not match the task contract')]:
                with final_core.connect() as db:
                    target=settled[0]['id'];row=db.execute('SELECT error_json FROM agent_tasks WHERE id=?',(target,)).fetchone()
                    problem=json.loads(row['error_json']);problem['model_output_reason']=reason
                    db.execute('UPDATE agent_tasks SET error_json=? WHERE id=?',(json.dumps(problem),target))
                page.locator('#teamRefresh').click()
                page.get_by_text(caption,exact=True).wait_for()
                assert len(model_hits)==protocol_count
            original_problem=dict(problem)
            for reason,caption in [('unapproved_support_ids','Model cited supporting evidence outside this input'),('counterevidence_set_mismatch','Model omitted or invented counterevidence references')]:
                semantic_problem={k:v for k,v in original_problem.items() if k not in ('model_output_reason','model_output_diagnostics')}
                semantic_problem['model_contract_reason']=reason
                with final_core.connect() as db:
                    db.execute('UPDATE agent_tasks SET error_json=? WHERE id=?',(json.dumps(semantic_problem),target))
                page.locator('#teamRefresh').click()
                page.get_by_text(caption,exact=True).wait_for()
                assert len(model_hits)==protocol_count
            problem=original_problem
            with final_core.connect() as db:
                problem['model_output_reason']='invalid_json'
                db.execute('UPDATE agent_tasks SET error_json=? WHERE id=?',(json.dumps(problem),target))
            page.locator('#teamRefresh').click()
            page.get_by_text('Output is not complete valid JSON',exact=True).first.wait_for()
            with final_core.connect() as db:
                db.execute("UPDATE agent_tasks SET status='paused',error_json=? WHERE id=?",(json.dumps({'code':'model_call_interrupted_review','call_states':['settled'],'automatic_retry':False}),target))
            page.locator('#teamRefresh').click()
            page.get_by_text('Model call interrupted; review ledger and saved results; no automatic retry',exact=True).wait_for()
            before_execution_writes=len(writes)
            page.locator(f'[data-team-execution="{live_group}"]').click()
            execution=page.get_by_role('dialog',name='Execution and recovery status',exact=True)
            execution.get_by_text('Dispatcher stopped',exact=True).wait_for()
            assert 'Without an enabled resume policy, app restart does not start tasks.' in execution.inner_text()
            assert 'Started or unknown-usage tasks are never automatically replayed.' in execution.inner_text()
            assert 'Interrupted model tasks requiring review: 1' in execution.inner_text()
            page.set_viewport_size({'width':430,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            execution.get_by_role('button',name='Close',exact=True).click()
            page.set_viewport_size({'width':1440,'height':1000})
            assert len(writes)==before_execution_writes and len(model_hits)==protocol_count
            with final_core.connect() as db:
                db.execute('UPDATE agent_tasks SET error_json=? WHERE id=?',(json.dumps(problem),target))
            page.locator('#teamRefresh').click()
            page.get_by_text('Output is not complete valid JSON',exact=True).first.wait_for()
            # Recorded blocked-policy fixture refers to the real rejected-output
            # task and its settled protocol ledger. This does not replay a model.
            import research_resume
            with final_core.connect() as db:
                reviewed_task=db.execute('SELECT * FROM agent_tasks WHERE id=?',(target,)).fetchone()
                reviewed_group=reviewed_task['group_id'];review_key='research-resume:'+reviewed_group
                previous_policy=db.execute('SELECT value FROM app_metadata WHERE key=?',(review_key,)).fetchone()
                previous_policy=previous_policy['value'] if previous_policy else None
                original_status=reviewed_task['status'];original_error=reviewed_task['error_json']
                db.execute("UPDATE agent_tasks SET status='paused',error_json=? WHERE id=?",(json.dumps({'code':'model_output_invalid_usage_settled','model_output_reason':'invalid_json','model_finish_reason':'stop','raw':'private-error-must-not-leak'}),target))
                reviewed_task=db.execute('SELECT * FROM agent_tasks WHERE id=?',(target,)).fetchone()
                research_resume.write(db,reviewed_group,{'state':'blocked','revision':__import__('uuid').uuid4().hex,'approved_at':final_core.utcnow(),'binding':{},'task_hashes':{target:research_resume.task_hash(reviewed_task)}})
            before_review_writes=len(writes)
            page.locator('#teamRefresh').click()
            page.locator(f'[data-team-resume-policy="{reviewed_group}"]').click()
            blocked_policy=page.get_by_role('dialog',name='Automatic resume settings',exact=True)
            blocked_policy.locator('[data-resume-review]').get_by_text('Approved tasks requiring review',exact=True).wait_for()
            review_text=blocked_policy.locator('[data-resume-review]').inner_text()
            assert target in review_text and 'Output is not complete valid JSON' in review_text
            assert '"settled":1' in review_text and 'private-error-must-not-leak' not in blocked_policy.inner_text()
            blocked_policy.locator('[data-resume-review-group]').click()
            page.wait_for_function('(id)=>document.querySelector("#agentsContent tbody")?.textContent.includes(id)',arg=target)
            assert reviewed_group in page.locator('.team-group.selected').inner_text()
            assert len(writes)==before_review_writes and len(model_hits)==protocol_count
            page.locator('#teamAllTasks').click()
            with final_core.connect() as db:
                db.execute('UPDATE agent_tasks SET status=?,error_json=? WHERE id=?',(original_status,original_error,target))
                if previous_policy is None:db.execute('DELETE FROM app_metadata WHERE key=?',(review_key,))
                else:db.execute('UPDATE app_metadata SET value=? WHERE key=?',(previous_policy,review_key))
            page.locator('#teamRefresh').click()
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            # Explicit isolated fixture setup for the new durable policy UI.
            import v5_runtime as runtime
            auto_profile=runtime.create_profile(runtime.ProfileCreate(name='Resume UI profile',mode='local',config=runtime.ProfileConfig(local_provider_ids=['bad-ui-model'])))
            with final_core.connect() as db:
                auto_group=db.execute("SELECT g.id FROM research_groups g JOIN agent_tasks t ON t.group_id=g.id WHERE g.status='active' AND t.status='queued' AND t.attempt=0 GROUP BY g.id LIMIT 1").fetchone()[0]
                db.execute('UPDATE research_groups SET runtime_profile_id=? WHERE id=?',(auto_profile['id'],auto_group))
                # Earlier stale-source review intentionally changed this node body.
                # Restore the registered fixture before approving new automatic inputs.
                db.execute('UPDATE research_nodes SET body=? WHERE id=?',('https://team-ui.example.test/object',source))
                current_source=db.execute('SELECT * FROM research_nodes WHERE id=?',(source,)).fetchone()
                for pending in db.execute("SELECT * FROM agent_tasks WHERE group_id=? AND status='queued' AND attempt=0",(auto_group,)).fetchall():
                    capsule=json.loads(pending['context_capsule_json']);capsule['research_input_hashes']={source:v5_workers._node_digest(current_source)}
                    db.execute('UPDATE agent_tasks SET context_capsule_json=? WHERE id=?',(json.dumps(capsule),pending['id']))
            page.locator('#teamRefresh').click()
            page.locator(f'[data-team-resume-policy="{auto_group}"]').click()
            policy=page.get_by_role('dialog',name='Automatic resume settings',exact=True)
            assert policy.locator('[data-candidate-limit]').input_value()=='0'
            policy.locator('[data-candidate-limit]').select_option('1')
            policy.locator('[data-preview]').click()
            policy.locator('[data-candidate-approved]').wait_for()
            policy.locator('[data-approved]').check();assert policy.locator('[data-enable]').is_disabled()
            policy.locator('[data-candidate-approved]').check();assert policy.locator('[data-enable]').is_enabled()
            policy.locator('[data-candidate-limit]').select_option('0')
            assert policy.locator('[data-enable]').count()==0
            policy.locator('[data-preview]').click();policy.locator('[data-approved]').wait_for()
            assert policy.locator('[data-enable]').is_disabled()
            with final_core.connect() as db:
                budget=json.loads(db.execute('SELECT budget_json FROM research_groups WHERE id=?',(auto_group,)).fetchone()[0]);budget['max_concurrency']=1 if budget['max_concurrency']!=1 else 2
                db.execute('UPDATE research_groups SET budget_json=? WHERE id=?',(json.dumps(budget),auto_group))
            flags['hold_resume_save']=True
            policy.locator('[data-approved]').check();policy.locator('[data-enable]').click()
            assert policy.locator('[data-candidate-limit]').is_disabled()
            assert policy.locator('[data-approved]').is_disabled()
            assert delayed
            held_route,held_response=delayed.pop();flags['hold_resume_save']=False
            held_route.fulfill(status=held_response.status_code,body=held_response.content,content_type='application/json')
            policy.get_by_text('Resume proposal changed; review again',exact=True).wait_for()
            assert policy.locator('[data-candidate-limit]').is_enabled()
            assert policy.locator('[data-enable]').count()==0
            policy.locator('[data-preview]').click();policy.locator('[data-approved]').wait_for()
            policy.locator('[data-approved]').check();policy.locator('[data-enable]').click()
            policy.locator('[data-disable]').wait_for(state='visible')
            assert client.get(f'/api/v1/workers/research/groups/{auto_group}/resume-policy').json()['state']=='enabled'
            policy.locator('[data-disable]').click()
            policy.get_by_text('New claims stopped; inflight tasks may finish.',exact=True).wait_for()
            assert client.get(f'/api/v1/workers/research/groups/{auto_group}/resume-policy').json()['state']=='disabled'
            expected_unstarted=client.get(f'/api/v1/workers/research/groups/{auto_group}/resume-policy').json()['task_progress']['unstarted']
            assert f'Unstarted: {expected_unstarted}' in policy.locator('[data-progress]').inner_text()
            policy.locator('[data-preview]').click();policy.locator('[data-approved]').wait_for()
            policy.locator('[data-approved]').check()
            # Change the persisted policy in another actor, then wait for actual
            # read-only polling to clear the now-stale approval without a click.
            with final_core.connect() as db:
                stored=json.loads(db.execute('SELECT value FROM app_metadata WHERE key=?',('research-resume:'+auto_group,)).fetchone()[0])
                stored['revision']=__import__('uuid').uuid4().hex
                db.execute('UPDATE app_metadata SET value=? WHERE key=?',(json.dumps(stored),'research-resume:'+auto_group))
            poll_writes=len(writes)
            policy.get_by_text('Policy changed; old approval cleared. Preview and review again.',exact=True).wait_for(timeout=12000)
            assert policy.locator('[data-enable]').count()==0
            policy.locator('[data-refresh-policy]').click()
            assert len(writes)==poll_writes and len(model_hits)==protocol_count
            # Recorded-policy history fixture exercises the exact-ID result link;
            # candidate creation itself is covered by worker/API tests.
            with final_core.connect() as db:
                stored['candidate_limit']=2
                stored['candidate_handoff']={'state':'observations_only','run_id':'ui-real-run','candidate_ids':[],'task_offset':1,'review_required':0,'non_security_drafts':3}
                db.execute('UPDATE app_metadata SET value=? WHERE key=?',(json.dumps(stored),'research-resume:'+auto_group))
            policy.locator('[data-refresh-policy]').click()
            policy.get_by_text('Asset observations only; no automatic security candidates',exact=False).wait_for()
            assert 'Asset drafts retained: 3' in policy.locator('[data-candidate-status]').inner_text()
            assert policy.locator('[data-candidate-follow]').count()==0
            with final_core.connect() as db:
                stored['candidate_handoff']={'state':'limit_reached','run_id':'ui-real-run','candidate_ids':expected_ids,'task_offset':1,'review_required':0}
                db.execute('UPDATE app_metadata SET value=? WHERE key=?',(json.dumps(stored),'research-resume:'+auto_group))
            policy.locator('[data-refresh-policy]').click()
            policy.locator('[data-candidate-follow]').wait_for()
            assert 'Unverified candidates saved: 2' in policy.locator('[data-candidate-status]').inner_text()
            policy.locator('[data-candidate-follow]').click()
            targeted=page.get_by_role('dialog',name='Review independent replay batch',exact=True)
            targeted.locator('[data-batch-authorized]').wait_for()
            assert all(identifier in targeted.inner_text() for identifier in expected_ids)
            assert 'Unrelated earlier candidate' not in targeted.inner_text()
            reads_after_close=len(policy_reads)
            page.wait_for_timeout(5500)
            assert len(policy_reads)==reads_after_close
            assert len(writes)==poll_writes and len(model_hits)==protocol_count
            targeted.locator('[data-close]').click()
            page.get_by_role('button',name='Agents',exact=True).click()
            page.locator('#teamRefresh').click()
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            with final_core.connect() as db:
                stored.pop('candidate_handoff');stored['candidate_limit']=0
                db.execute('UPDATE app_metadata SET value=? WHERE key=?',(json.dumps(stored),'research-resume:'+auto_group))
            assert len(model_hits)==protocol_count
            before_execution_writes=len(writes)
            output = ROOT / "build/acceptance/v5-team-ui"
            output.mkdir(parents=True, exist_ok=True)
            for language in ("en", "zh-CN"):
                page.set_viewport_size({"width": 1440, "height": 1000})
                if page.locator("html").get_attribute("lang") != language:
                    page.locator("#languageToggle").click()
                assert ('known usage settled' if language=='en' else '已知用量已结算') in page.locator('#agentsContent').inner_text()
                for theme in ("dark", "light"):
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    if page.locator("html").get_attribute("data-theme") != theme:
                        page.locator("#themeToggle").click()
                    for width in (1440, 680):
                        page.set_viewport_size({"width": width, "height": 1000})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (language, theme, width)
                        page.screenshot(path=str(output / f"{language}-{theme}-{width}.png"))
                        page.locator(f'[data-team-execution="{live_group}"]').click()
                        diagnostic=page.get_by_role('dialog',name='Execution and recovery status' if language=='en' else '执行状态与恢复提示',exact=True)
                        diagnostic.get_by_text('Dispatcher stopped' if language=='en' else '调度已停止',exact=True).wait_for()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ('execution',language,theme,width)
                        page.screenshot(path=str(output / f"execution-{language}-{theme}-{width}.png"))
                        diagnostic.get_by_role('button',name='Close' if language=='en' else '关闭',exact=True).click()
                        page.locator(f'[data-team-resume-policy="{auto_group}"]').click()
                        policy=page.get_by_role('dialog',name='Automatic resume settings' if language=='en' else '自动续跑设置',exact=True)
                        policy.locator('[data-status][data-state=disabled]').wait_for()
                        assert ('Disabled' if language=='en' else '已关闭') in policy.locator('[data-status]').inner_text()
                        assert ('Not observed' if language=='en' else '尚未观察到') in policy.locator('[data-status]').inner_text()
                        assert f"{'Unstarted' if language=='en' else '未开始'}: {expected_unstarted}" in policy.locator('[data-progress]').inner_text()
                        # Exercise the real missing-Profile rejection in both languages,
                        # without approving or dispatching any task.
                        with final_core.connect() as db:
                            db.execute('UPDATE research_groups SET runtime_profile_id=NULL WHERE id=?',(auto_group,))
                        try:
                            policy.locator('[data-preview]').click()
                            expected_error='Automatic resume requires an explicit runtime Profile on this group' if language=='en' else '该研究组尚未绑定明确的模型 Profile，无法启用续跑；请核对模型配置后使用绑定 Profile 的研究组。'
                            policy.locator('[data-message]').get_by_text(expected_error,exact=True).wait_for()
                            assert policy.locator('[data-enable]').count()==0
                        finally:
                            with final_core.connect() as db:
                                db.execute('UPDATE research_groups SET runtime_profile_id=? WHERE id=?',(auto_profile['id'],auto_group))
                        policy.locator('[data-preview]').click()
                        policy.locator('[data-approved]').wait_for()
                        assert policy.locator('[data-message]').inner_text()==''
                        assert policy.locator('[data-enable]').is_disabled()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        policy.locator('[data-close]').click()
                        before_execution_writes+=2
                        assert len(writes)==before_execution_writes and len(model_hits)==protocol_count
            flags['legacy_resume']=True
            page.locator(f'[data-team-resume-policy="{auto_group}"]').click()
            legacy_policy=page.get_by_role('dialog',name='自动续跑设置',exact=True)
            legacy_policy.locator('[data-status][data-state=completed]').wait_for()
            assert '策略已结束；请核对任务记录' in legacy_policy.locator('[data-status]').inner_text()
            assert '未开始: 未提供' in legacy_policy.locator('[data-progress]').inner_text()
            assert '本清单草稿已保存' not in legacy_policy.inner_text()
            legacy_policy.locator('[data-close]').click();flags['legacy_resume']=False
            assert len(writes)==before_execution_writes and len(model_hits)==protocol_count
            assert not errors, errors
            browser.close()
        print(json.dumps({"groups": 3, "tasks": 11, "ui_writes": len(writes), "page_errors": errors,
                          "checks": ["1/2/8 queue creation", "stale preview", "locale preservation",
                                     "read failure", "group controls", "reload", "runner page", "invalid output usage settled",
                                     "no replay after reload", "read-only execution marker and lease status", "resume exact preview stale rejection enable disable", "localized actual progress and legacy missing-data guard", "8 display combinations including execution dialog"], 'model_protocol_requests':len(model_hits),
                          "scope": "isolated fixture Run, real APIs and model HTTP protocol fixture; not actual model inference or target discovery"}, ensure_ascii=False))


if __name__ == "__main__":
    run()
