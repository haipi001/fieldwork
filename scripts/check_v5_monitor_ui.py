"""Exercise real V5 UI with isolated monitor fixtures; no local telemetry writes."""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    state = {"status": None, "fail": False, "scan_state": "current", "policy": None, "policy_id": None}
    writes, errors = [], []

    def detail(identifier):
        return {"id": identifier, "events": [], "candidates": [], "findings": [],
                "monitor": {"status": state["status"] if identifier == "live" else "stopped",
                            "scan_state": "paused" if state["status"] == "paused" else state["scan_state"],
                            "last_scan_at": "2026-10-02T10:00:00Z", "desktop": {"coverage": {}}}}

    def route_request(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method == "POST":
            writes.append(path)
            if path == "/api/v1/agent-audit/monitor/live/policy":
                body = request.post_data_json
                assert body['confirmed'] is True
                assert body['expected_id'] == state['policy_id']
                state['policy'], state['policy_id'] = body['policy'], 'policy-1'
                route.fulfill(json={"id": state['policy_id'], "policy": state['policy']})
                return
            assert path in {"/api/v1/agent-audit/monitor/start", *[f"/api/v1/agent-audit/monitor/live/{a}" for a in ("pause", "resume", "scan")]}
            action = path.rsplit("/", 1)[-1]
            if action != "scan":
                state["status"] = "paused" if action == "pause" else "active"
            route.fulfill(json=detail("live"))
        elif path == "/v5":
            route.fulfill(path=str(ROOT / "templates/v5.html"), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(path=str(ROOT / path.lstrip("/")))
        elif path == "/api/v1/agent-audit/audits":
            if state["fail"]:
                route.fulfill(status=503, json={"detail": "fixture unavailable"})
                return
            audits = [{"id": "old", "name": "Historical audit", "collector_kind": "desktop", "monitor_status": "stopped"}]
            if state["status"]:
                audits.append({"id": "live", "name": "Current monitor", "collector_kind": "desktop", "monitor_status": state["status"]})
            route.fulfill(json=audits)
        elif path.startswith("/api/v1/agent-audit/audits/"):
            route.fulfill(json=detail(path.rsplit("/", 1)[-1]))
        elif path == "/api/v1/agent-audit/monitor/live/windows":
            route.fulfill(json={"windows": [{"event_count": 2, "status": "policy_required", "signals": []}], "pending_events": 0, "recovery": None})
        elif path == "/api/v1/agent-audit/monitor/live/policy":
            route.fulfill(json={"id": state['policy_id'], "policy": state['policy']})
        elif path == "/api/v1/findings":
            route.fulfill(json={"verified": [], "candidates": []})
        elif path in {"/api/v1/engagements", "/api/v1/task-center", "/api/v1/capabilities"}:
            route.fulfill(json=[])
        else:
            route.fulfill(status=503, json={"detail": "Outside monitor fixture"})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", route_request)
        page.goto("http://127.0.0.1:8012/v5#sentinel")
        page.wait_for_function("!document.querySelector('#monitorToggle').disabled")
        assert not writes
        assert page.locator("#newResearch").is_hidden()
        assert page.locator('#researchMode option[value="agent_audit"]').count() == 0
        page.locator("#monitorToggle").click()
        page.wait_for_function("document.querySelector('#monitorToggle').textContent === '暂停监控' && !document.querySelector('#monitorToggle').disabled")
        assert "缺少策略，无法判定违规" in page.locator("#monitorAnalysis").inner_text()
        page.locator('.monitor-history>summary').click()
        page.locator("#sentinelAudit").select_option("old")
        page.locator("#monitorToggle").click()
        page.wait_for_function("document.querySelector('#monitorToggle').textContent === '恢复监控' && !document.querySelector('#monitorToggle').disabled")
        assert page.locator("#monitorScan").is_disabled()
        page.locator("#monitorToggle").click()
        page.wait_for_function("!document.querySelector('#monitorScan').disabled")
        page.locator("#monitorScan").click()
        page.wait_for_function("document.querySelector('#monitorMessage').textContent.includes('这不是漏洞确认结果')")
        assert writes == ["/api/v1/agent-audit/monitor/start", *[f"/api/v1/agent-audit/monitor/live/{a}" for a in ("pause", "resume", "scan")]]
        page.locator('#monitorControl summary').click()
        page.locator('#monitorPolicyRead').click()
        page.locator('#monitorPolicyForm').wait_for(state='visible')
        page.locator('#monitorPolicyForm [name="allowed_network_hosts"]').fill('example.test')
        page.locator('#monitorPolicyForm [name="internet_access"]').check()
        before_language_writes = list(writes)
        page.locator('#languageToggle').click()
        assert page.locator('#monitorToggle').inner_text() == 'Pause monitoring'
        assert 'Policy missing' in page.locator('#monitorAnalysis').inner_text()
        assert page.locator('#monitorPolicyForm [name="allowed_network_hosts"]').input_value() == 'example.test'
        assert page.locator('#monitorPolicyForm [name="internet_access"]').is_checked()
        assert page.locator('#monitorPolicyRead').text_content() == 'Read current policy'
        assert page.locator('#monitorPolicyForm [name="allowed_tools"]').get_attribute('placeholder') == 'One entry per line'
        assert writes == before_language_writes
        page.locator('#languageToggle').click()
        assert page.locator('#monitorPolicyRead').inner_text() == '读取当前策略'
        assert page.locator('#monitorPolicyForm [name="allowed_network_hosts"]').input_value() == 'example.test'
        page.locator('#monitorPolicyForm [type="submit"]').click()
        assert len(writes) == 4  # Required confirmation prevents an accidental save.
        page.locator('#monitorPolicyConfirm').check()
        page.locator('#monitorPolicyForm [type="submit"]').click()
        page.wait_for_function("document.querySelector('#monitorPolicyMessage').textContent.includes('策略已保存')")
        assert state['policy']['allowed_network_hosts'] == ['example.test']
        assert state['policy']['internet_access'] is True
        assert state['policy']['shell_access'] is False
        state["scan_state"] = "delayed"
        page.locator("#monitorRefresh").click()
        page.wait_for_function("document.querySelector('#monitorState').textContent.includes('采集延迟')")
        assert page.locator('#monitorControl').get_attribute('data-status') == 'error'
        state["fail"] = True
        page.locator("#monitorRefresh").click()
        page.wait_for_function("document.querySelector('#monitorMessage').textContent.includes('读取失败')")
        assert page.locator("#monitorToggle").is_disabled()
        page.locator('#languageToggle').click()
        assert 'Monitor status unavailable' in page.locator('#monitorState').inner_text()
        assert 'Read failed' in page.locator('#monitorMessage').inner_text()
        assert page.locator('#monitorToggle').is_disabled()
        page.locator('#languageToggle').click()
        state["fail"] = False
        page.locator("#monitorRefresh").click()
        page.wait_for_function("!document.querySelector('#monitorToggle').disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("localStorage.setItem('fieldwork-v5-language', 'en')")
        page.reload()
        page.wait_for_function("!document.querySelector('#monitorToggle').disabled")
        assert page.locator('#monitorControl summary').inner_text() == 'Monitoring policy'
        assert page.locator('#monitorPolicyRead').text_content() == 'Read current policy'
        assert page.locator('#monitorToggle').inner_text() == 'Pause monitoring'
        assert not errors, errors
        browser.close()
    print("PASS: fixed monitor page, explicit writes only, lifecycle, history isolation, delayed/error recovery, mobile layout")


if __name__ == "__main__":
    run()
