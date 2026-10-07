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
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app
import final_core
import v5_research_worker as research
from tests.test_final import create_ready


def run():
    with tempfile.TemporaryDirectory(prefix="fieldwork-team-ui-") as directory, ExitStack() as stack:
        root = Path(directory)
        for module, name, value in ((app, "DB", root / "ui.db"), (app, "DATA", root),
                                    (final_core, "DB", root / "ui.db"), (final_core, "LOCAL_DATA_ROOT", root)):
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch.dict(os.environ, {"FIELDWORK_SESSION_TOKEN": secrets.token_urlsafe(48)}))
        app.prepare_database_upgrade(root / "ui.db", root / "backups", ROOT)
        app.init_db()
        final_core.init_final_db()
        app.apply_v5_schema(root / "ui.db")
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
        flags = {"hold_preview": False, "fail_groups": False}

        def route_request(route):
            request = route.request
            parsed = urlparse(request.url)
            path = parsed.path
            assert parsed.hostname == "127.0.0.1", "unexpected target request"
            if path == "/v5":
                route.fulfill(path=str(ROOT / "templates/v5.html"), content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(ROOT / path.lstrip("/")))
            elif (path in {"/api/v1/engagements", "/api/v1/runs", "/api/v1/runtime/config"}
                  or path.startswith("/api/v1/orchestration/")
                  or path.startswith("/api/v1/workers/research/")
                  or path == f"/api/v1/engagements/{engagement['id']}/campaigns"):
                if request.method != "GET":
                    writes.append(path)
                if flags["fail_groups"] and path == "/api/v1/orchestration/groups":
                    route.fulfill(status=503, json={"detail": "injected read failure"})
                    return
                response = client.request(request.method, path + ("?" + parsed.query if parsed.query else ""),
                                          content=request.post_data,
                                          headers={"Content-Type": "application/json"})
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
                page.locator("#teamResearchers").fill(str(count))
                page.locator("#teamConcurrency").fill(str(min(count, 2)))
                page.locator("#teamPreview").click()
                page.wait_for_function("!document.querySelector('#teamCommit').disabled")
                assert f"{count} 个逻辑任务" in page.locator("#teamPreviewResult").inner_text()
                page.locator("#teamAccepted").check()
                page.locator("#teamCommit").click()
                page.wait_for_function("!document.querySelector('#teamDialog').open")
                page.wait_for_function(f"document.querySelectorAll('#agentsContent tbody tr').length === {count}")
            assert len(client.get(f"/api/v1/orchestration/groups?campaign_id={campaign['id']}").json()["items"]) == 3
            assert len(client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}").json()["items"]) == 11

            # A response from an older edited plan must never re-enable commit.
            page.locator("#teamCreate").click()
            flags["hold_preview"] = True
            page.locator("#teamPreview").click()
            page.wait_for_function("document.querySelector('#teamPreview').disabled")
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
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            assert len(model_hits)==live_count
            assert '17 / 12' in page.locator('#agentsContent').inner_text()
            assert 'fixture-private-output' not in page.locator('#agentsContent').inner_text()
            page.reload()
            page.wait_for_function("document.querySelector('#agentsContent').innerText.includes('known usage settled')")
            assert len(model_hits)==live_count
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
            assert not errors, errors
            browser.close()
        print(json.dumps({"groups": 3, "tasks": 11, "ui_writes": len(writes), "page_errors": errors,
                          "checks": ["1/2/8 queue creation", "stale preview", "locale preservation",
                                     "read failure", "group controls", "reload", "runner page", "invalid output usage settled",
                                     "no replay after reload", "8 display combinations"], 'model_protocol_requests':len(model_hits),
                          "scope": "isolated fixture Run, real APIs and model HTTP protocol fixture; not actual model inference or target discovery"}, ensure_ascii=False))


if __name__ == "__main__":
    run()
