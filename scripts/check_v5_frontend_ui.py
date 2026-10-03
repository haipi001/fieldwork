"""Browser smoke for Fieldwork V5: isolated reads and write-flow fixtures.

No running service is required:
    FIELDWORK_V5_BASE=http://127.0.0.1:8011 .venv/bin/python scripts/check_v5_frontend_ui.py

All requests are intercepted; unexpected writes fail the test. No test target
or run is persisted, and no scan reaches the network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


BASE = os.environ.get("FIELDWORK_V5_BASE", "http://127.0.0.1:8011").rstrip("/")
CHROME = os.environ.get(
    "FIELDWORK_CHROME_PATH",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
VIEWS = (
    "campaign", "graph", "hypotheses", "agents", "evolution", "tasks", "runners", "tools",
    "sentinel", "events", "incidents", "evidence", "verification", "findings",
    "reports", "runtime", "extensions", "settings",
)
WIDTHS = (400, 640, 800, 1024, 1280, 1440)
THEMES = ("dark", "light")


def isolated_page(browser, **kwargs):
    """Serve repository assets and fixtures; never reach the local service."""
    page = browser.new_page(**kwargs)
    root = Path(__file__).resolve().parents[1]

    def serve(route):
        path = urlparse(route.request.url).path
        assert route.request.method == "GET", f"Unexpected write: {path}"
        if path == "/v5":
            route.fulfill(path=str(root / "templates/v5.html"), content_type="text/html")
        elif path.startswith("/static/"):
            route.fulfill(path=str(root / path.lstrip("/")))
        elif path in ("/api/v1/engagements", "/api/v1/task-center", "/api/v1/capabilities", "/api/v1/agent-audit/audits"):
            route.fulfill(json=[])
        elif path == "/api/v1/findings":
            route.fulfill(json={"verified": [], "candidates": []})
        else:
            route.fulfill(status=503, json={"detail": "Outside isolated fixture"})

    page.route("**/*", serve)
    return page


def assert_task_controls_with_isolated_api(browser) -> None:
    page = isolated_page(browser, viewport={"width": 1280, "height": 900})
    status = {"value": "running"}
    actions: list[str] = []

    def tasks(route) -> None:
        route.fulfill(json={"items": [{
            "id": "run-control-isolated",
            "engagement_id": "engagement-control-isolated",
            "status": status["value"],
            "resume_supported": status.get("resume_supported", True),
            "resume_unavailable_reason": "当前运行模式不支持恢复",
            "current_stage": "verification",
            "completed_stages": 5,
            "last_event": "隔离控制验收",
        }]})

    def run_action(route) -> None:
        action = route.request.url.rsplit("/", 1)[-1]
        actions.append(action)
        if action == "pause" and actions.count("pause") == 1:
            route.fulfill(status=409, json={"detail": "任务状态已变化，请刷新队列"})
            return
        status["value"] = {"pause": "paused", "resume": "running", "stop": "stopped"}[action]
        route.fulfill(json={"id": "run-control-isolated", "status": status["value"]})

    page.route("**/api/v1/task-center?mode=traditional", tasks)
    page.route("**/api/v1/runs/run-control-isolated/pause", run_action)
    page.route("**/api/v1/runs/run-control-isolated/resume", run_action)
    page.route("**/api/v1/runs/run-control-isolated/stop", run_action)
    page.goto(f"{BASE}/v5#tasks", wait_until="domcontentloaded")
    page.locator('#tasksContent [data-run-action="pause"]').click()
    assert page.locator("#runControlDialog").evaluate("(el) => el.open")
    assert not actions
    page.locator("#runControlCommit").click()
    page.get_by_text("操作失败：任务状态已变化，请刷新队列").wait_for()
    assert page.locator("#runControlDialog").evaluate("(el) => el.open")
    assert status["value"] == "running"
    page.locator("#runControlCommit").click()
    page.get_by_text("任务已暂停").wait_for()
    page.locator('#tasksContent [data-run-action="resume"]').wait_for()
    page.locator('#tasksContent [data-run-action="resume"]').click()
    page.locator("#runControlCommit").click()
    page.get_by_text("任务已恢复").wait_for()
    page.locator('#tasksContent [data-run-action="stop"]').click()
    page.locator("#runControlCommit").click()
    page.get_by_text("任务已停止").wait_for()
    assert actions == ["pause", "pause", "resume", "stop"]
    assert page.locator('#tasksContent [data-run-action="pause"]').count() == 0
    assert page.locator('#tasksContent [data-run-action="resume"]').count() == 0
    assert page.locator('#tasksContent [data-run-action="stop"]').count() == 0
    status.update(value="paused", resume_supported=False)
    page.locator("#taskRefresh").click()
    page.locator('#tasksContent [data-run-action="resume"]').wait_for()
    assert page.locator('#tasksContent [data-run-action="resume"]').is_disabled()
    assert page.locator('#tasksContent [data-run-action="resume"]').inner_text() == "恢复不可用"
    page.close()


def assert_inspector_modal_with_isolated_api(browser) -> None:
    page = isolated_page(browser, viewport={"width": 1280, "height": 900})
    page.route("**/api/v1/engagements?mode=traditional", lambda route: route.fulfill(json=[{
        "id": "engagement-inspector-check",
        "name": "只读详情焦点验收",
        "mode": "traditional",
        "status": "draft",
        "normalized_target": "https://authorized.example",
    }]))
    page.goto(f"{BASE}/v5#campaign", wait_until="domcontentloaded")
    page.locator(".home-archive summary").click()
    page.locator("#engagementList .data-row").first.click()
    assert page.locator("#detailInspector").get_attribute("aria-hidden") == "false"
    assert page.evaluate("document.querySelector('#content').inert && document.querySelector('#sidebar').inert")
    assert page.evaluate("document.activeElement.id") == "inspectorClose"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.closest('#detailInspector') !== null")
    page.keyboard.press("Control+k")
    assert page.locator("#commandPalette").evaluate("(el) => el.open")
    assert page.locator("#detailInspector").get_attribute("aria-hidden") == "true"
    assert not page.evaluate("document.querySelector('#content').inert || document.querySelector('#sidebar').inert")
    page.keyboard.press("Escape")
    page.locator("#engagementList .data-row").first.click()
    page.keyboard.press("Escape")
    assert page.locator("#detailInspector").get_attribute("aria-hidden") == "true"
    assert not page.evaluate("document.querySelector('#content').inert || document.querySelector('#sidebar').inert")
    assert page.evaluate("document.activeElement.matches('#engagementList .data-row')")
    page.close()


def assert_research_creation_with_isolated_api(browser) -> None:
    """Test the primary creation flow without adding a real research target."""
    page = isolated_page(browser, viewport={"width": 1280, "height": 900})
    created = {
        "id": "engagement-isolated-ui-check",
        "name": "隔离前端验收",
        "raw_target": "https://authorized.example",
        "normalized_target": "https://authorized.example",
        "mode": "traditional",
        "status": "draft",
    }
    submitted: list[dict] = []
    confirmed: list[bool] = []
    plans: list[dict] = []
    starts: list[dict] = []

    def engagements(route) -> None:
        if route.request.method == "POST":
            submitted.append(route.request.post_data_json)
            route.fulfill(status=201, json=created)
        else:
            route.fulfill(json=[{**created, "status": "ready" if confirmed else "draft"}] if submitted else [])

    def campaign(route) -> None:
        route.fulfill(json={
            **created,
            "status": "ready" if confirmed else "draft",
            "confirmed_at": "2026-09-30T00:00:00Z" if confirmed else None,
            "scope": {
                "allowed_targets": ["https://authorized.example"],
                "allowed_actions": ["read", "analyze"],
                "denied_actions": ["destructive"],
                "authorization_notes": "仅对隔离测试目标进行授权校验",
            },
            "policy": {"max_requests": 100, "max_runtime_minutes": 30},
        })

    def confirm(route) -> None:
        confirmed.append(True)
        campaign(route)

    def execution_plan(route) -> None:
        plans.append(route.request.post_data_json)
        route.fulfill(json={
            "target": "https://authorized.example",
            "ready": len(plans) > 1,
            "budget": {"requests": 100, "requests_per_second": 1, "runtime_minutes": 30},
            "tools": [],
            "blockers": [] if len(plans) > 1 else [{"message": "隔离验收中禁止启动真实扫描"}],
            "warnings": [],
            "denied_actions": ["destructive"],
        })

    def start(route) -> None:
        starts.append(route.request.post_data_json)
        route.fulfill(status=202, json={"id": "run-isolated-ui-check", "status": "queued"})

    page.route("**/api/v1/engagements?mode=traditional", engagements)
    page.route("**/api/v1/engagements", engagements)
    page.route(
        "**/api/v1/task-center?mode=traditional",
        lambda route: route.fulfill(json=[{
            "id": "run-isolated-ui-check",
            "engagement_id": created["id"],
            "status": "queued",
            "current_stage": "target",
            "completed_stages": 0,
        }] if starts else []),
    )
    page.route("**/api/v1/engagements/engagement-isolated-ui-check", campaign)
    page.route("**/api/v1/engagements/engagement-isolated-ui-check/confirm", confirm)
    page.route("**/api/v1/engagements/engagement-isolated-ui-check/execution-plan", execution_plan)
    page.route("**/api/v1/engagements/engagement-isolated-ui-check/start", start)
    page.goto(f"{BASE}/v5#campaign", wait_until="domcontentloaded")
    page.locator("#newResearch").click()
    assert page.locator("#researchDialog").evaluate("(el) => el.open")
    page.locator("#researchName").fill("隔离前端验收")
    page.locator("#researchTarget").fill("https://authorized.example")
    page.locator("#researchScopeNotes").fill("仅对隔离测试目标进行授权校验")
    page.locator("#researchAuthorized").check()
    page.locator('#researchForm [type="submit"]').click()
    page.get_by_text("研究草稿已创建，尚未授权执行").wait_for()
    assert len(submitted) == 1
    assert submitted[0]["name"] == "隔离前端验收"
    assert submitted[0]["target"] == "https://authorized.example"
    assert submitted[0]["scope"]["authorization_notes"] == "仅对隔离测试目标进行授权校验"
    assert page.locator("#detailInspector").get_attribute("class").find("open") >= 0
    page.locator('#detailInspector [data-campaign-action="scope"]').click()
    page.wait_for_function("document.querySelector('#scopeDialog').open")
    assert page.locator("#scopeSummary").get_by_text("https://authorized.example").count() >= 1
    page.locator("#scopeAccepted").check()
    page.locator('#scopeForm [type="submit"]').click()
    page.get_by_text("ScopeSnapshot 已确认；执行前还需检查计划").wait_for()
    assert len(confirmed) == 1
    page.locator('#homePlan').click()
    page.wait_for_function("document.querySelector('#executionDialog').open")
    page.locator("#executionPlan").get_by_text("隔离验收中禁止启动真实扫描").wait_for()
    assert len(plans) == 1
    assert plans[0]["execution_mode"] == "real"
    assert page.locator("#executionCommit").is_disabled()
    assert not starts
    page.locator("#executionPreview").click()
    page.wait_for_function("!document.querySelector('#executionCommit').disabled")
    page.locator("#executionAccepted").check()
    page.locator("#executionCommit").click()
    page.get_by_text("受控任务 run-isolated-ui-check 已进入队列").wait_for()
    assert page.locator(".view.active").get_attribute("id") == "tasks"
    assert page.locator("#tasksContent").get_by_text("run-isolated-ui-check").count() >= 1
    assert len(starts) == 1
    assert starts[0]["execution_mode"] == "real"
    page.close()


def assert_findings_with_isolated_api(browser) -> None:
    """Exercise populated Findings without writing to the user's database."""
    verified = [
        {
            "id": f"finding-{index:04d}",
            "title": "授权边界复验结果" if index else "<img src=x onerror=alert(1)>",
            "target": "https://research.example/" + ("long-path-" * 14 if index == 0 else str(index)),
            "mode": "traditional",
            "status": "verified",
            "severity": "high",
            "lifecycle_status": "open",
            "evidence_ids": ["evidence-001"],
        }
        for index in range(1001)
    ]
    candidate = {**verified[0], "id": "candidate-must-not-appear", "status": "candidate"}
    detail = {
        **verified[0],
        "title": "授权边界复验结果",
        "verification": {
            "oracle": "independent",
            "receipt_id": "receipt-001",
            "expected": "未授权访问应被拒绝",
            "actual": "隔离复验中观察到返回",
            "attempt_id": "attempt-001",
        },
        "impact": {"description": "只读资源访问边界被突破"},
        "scope_snapshot_id": "scope-001",
        "evidence": [{
            "id": "evidence-001", "kind": "observation",
            "summary": "隔离环境复验记录", "created_at": "2026-09-30T00:00:00Z",
        }],
    }
    planned: list[dict] = []
    exports: list[str] = []
    for width in (400, 1440):
        page = isolated_page(browser, viewport={"width": width, "height": 900})
        errors: list[str] = []
        proof_calls: list[bool] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(
            "**/api/v1/findings?mode=traditional",
            lambda route: route.fulfill(json={"verified": verified + [candidate], "candidates": [candidate]}),
        )
        page.route(
            "**/api/v1/findings/finding-0000",
            lambda route: route.fulfill(json=detail),
        )
        page.route(
            "**/api/v1/findings/finding-0000/lifecycle",
            lambda route: route.fulfill(json={
                "finding_id": "finding-0000",
                "engagement_id": "engagement-findings-check",
                "last_seen_run_id": "run-original",
                "status": "retest_required" if planned else "fix_claimed",
                "remediation": {"description": "已提交修复声明，等待独立复测"},
                "retests": [{"status": "planned"}] if planned else [],
                "occurrence_count": 2,
                "fixed_gate": "修复声明不会关闭 Finding。",
            }),
        )
        page.route(
            "**/api/v1/task-center?mode=traditional",
            lambda route: route.fulfill(json=[{
                "id": "run-original",
                "engagement_id": "engagement-findings-check",
                "status": "completed",
            }, {
                "id": "run-new",
                "engagement_id": "engagement-findings-check",
                "status": "completed",
            }]),
        )
        page.route(
            "**/api/v1/findings/finding-0000/proof-capsule",
            lambda route: (proof_calls.append(True), route.fulfill(
                status=409, json={"detail": "历史回执不可验证"},
            )) if width == 1440 and len(proof_calls) < 2 else (proof_calls.append(True), route.fulfill(json={
                "finding_id": "finding-0000",
                "sha256": "historical-proof-sha256",
                "portability_status": "recorded_assertion_ready",
                "replay": {"kind": "http_authorization_read_v2"},
            })),
        )
        page.route(
            "**/api/v1/findings/finding-0000/retest-plans",
            lambda route: (planned.append(route.request.post_data_json), route.fulfill(
                status=201, json={"id": "retest-001", "finding_id": "finding-0000",
                                  "run_id": "run-new", "status": "planned"},
            )),
        )
        page.route(
            "**/api/v1/findings/finding-0000/reports/*/preview",
            lambda route: route.fulfill(json={
                "id": "preview-isolated",
                "finding_id": "finding-0000",
                "adapter": "Isolated report adapter",
                "content": "Verified finding report preview",
                "completeness": {
                    "ready": route.request.url.endswith("/hackerone/preview"),
                    "missing_required": [] if route.request.url.endswith("/hackerone/preview") else ["remediation"],
                    "missing_recommended": ["timeline"],
                },
            }),
        )
        page.route(
            "**/api/v1/findings/finding-0000/reports/*/export",
            lambda route: (exports.append(route.request.url), route.fulfill(
                status=202, json={
                    "id": "submission-isolated",
                    "status": "review_ready" if route.request.url.endswith("/hackerone/export") else "draft_incomplete",
                },
            )),
        )
        page.goto(f"{BASE}/v5#findings", wait_until="domcontentloaded")
        page.wait_for_function(
            "document.querySelector('#findingsList table tbody tr')?.textContent.includes('finding-0000')",
            timeout=20000,
        )
        assert page.locator("#findingsList tbody tr").count() == 20
        assert page.locator("#findingsPager").inner_text().startswith("1 / 51")
        assert page.locator("#findingsList").get_by_text("candidate-must-not-appear").count() == 0
        assert page.locator("#findingsList img").count() == 0
        page.locator('[data-finding-open="finding-0000"]').click()
        page.get_by_text("隔离环境复验记录").wait_for()
        assert page.locator("#findingDetail").get_by_text("receipt-001").count() == 1
        assert page.locator("#findingDetail").get_by_text("不校验当前有效性").count() == 1
        assert page.locator("#findingDetail").get_by_text("已提交修复声明，等待独立复测").count() == 1
        assert page.locator("#findingDetail").get_by_text("修复声明不会关闭 Finding。").count() == 1
        page.locator('#findingDetail [data-finding-evidence="finding-0000"]').click()
        assert page.locator("#findingEvidence").inner_text() == "证据记录"
        page.locator('#findingDetail [data-finding-proof="finding-0000"]').click()
        if width == 400:
            page.get_by_text("historical-proof-sha256").wait_for()
        else:
            page.get_by_text("证明包不可用").wait_for()
        page.locator('#findingDetail [data-finding-retest="finding-0000"]').click()
        assert page.locator("#findingRetestDialog").evaluate("(el) => el.open")
        assert page.locator("#findingRetestRun option").count() == 2
        page.locator("#findingRetestRun").select_option("run-new")
        page.locator("#findingRetestNote").fill("在修复版本上重新验证原始授权边界")
        page.locator("#findingRetestSubmit").click()
        page.get_by_text("定向复测计划 retest-001 已建立；新 Candidate 仍需验证").wait_for()
        assert len(planned) == (1 if width == 400 else 2)
        assert planned[-1]["run_id"] == "run-new"
        assert page.locator("#findingsList").get_by_text("candidate-must-not-appear").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        page.locator('#findingDetail [data-finding-report="finding-0000"]').click()
        assert page.locator(".view.active").get_attribute("id") == "reports"
        assert page.locator("#reportFinding").input_value() == "finding-0000"
        page.locator('#reportForm [type="submit"]').click()
        page.get_by_text("Verified finding report preview").wait_for()
        assert page.locator("#reportQuality").get_by_text("必填材料不完整").count() == 1
        if width == 400:
            page.get_by_text("历史证明包可读取；导出时后端仍会重新校验。").wait_for()
            assert page.locator("#reportExport").inner_text() == "导出待补草稿包"
            page.locator("#reportExport").click()
            page.get_by_text("待补草稿包 submission-isolated 已生成").wait_for()
        else:
            page.get_by_text("预览已生成，但导出受证明材料门槛阻止").wait_for()
            assert page.locator("#reportExport").is_disabled()
        page.locator("#reportPlatform").select_option("hackerone")
        assert page.locator("#reportExport").is_disabled()
        page.locator('#reportForm [type="submit"]').click()
        page.get_by_text("必填材料齐全，可供人工审核").wait_for()
        assert page.locator("#reportExport").inner_text() == "导出审核材料包"
        page.locator("#reportExport").click()
        page.get_by_text("审核材料包 submission-isolated 已生成").wait_for()
        assert len(exports) == (2 if width == 400 else 3)
        assert not errors, (width, errors)
        page.close()


def assert_view(page, view: str, width: int, theme: str) -> None:
    page.evaluate(
        "(id) => document.querySelector('[data-view=\"' + id + '\"]').click()",
        view,
    )
    result = page.evaluate(
        """(id) => {
            const el = document.getElementById(id);
            const rect = el.getBoundingClientRect();
            return {
                active: el.classList.contains("active"),
                visible: rect.width > 0 && rect.height > 0,
                heading: Boolean(el.querySelector("h1")),
                overflow: document.documentElement.scrollWidth - innerWidth,
            };
        }""",
        view,
    )
    assert result["active"] and result["visible"] and result["heading"], (
        width, theme, view, result
    )
    assert result["overflow"] <= 1, (width, theme, view, result)


def main() -> None:
    checked = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        try:
            for width in WIDTHS:
                for theme in THEMES:
                    page = isolated_page(browser, viewport={"width": width, "height": 900})
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"{BASE}/v5#campaign", wait_until="domcontentloaded")
                    page.wait_for_function(
                        "document.querySelector('#lastUpdated').textContent.includes('同步于')",
                        timeout=20000,
                    )
                    if theme == "light":
                        if width <= 760:
                            page.locator("#mobileMenu").click()
                            page.locator('[data-view="settings"]').click()
                            page.locator("#settingsThemeToggle").click()
                        else:
                            page.locator("#themeToggle").click()
                    assert page.locator("html").get_attribute("data-theme") == theme
                    page.wait_for_timeout(250)
                    expected_accent = "#315f88" if theme == "light" else "#abcbe9"
                    assert page.evaluate(
                        "() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()"
                    ) == expected_accent
                    for view in VIEWS:
                        assert_view(page, view, width, theme)
                        checked += 1
                    page.wait_for_timeout(300)
                    assert not errors, (width, theme, errors)
                    page.close()

            page = isolated_page(browser, viewport={"width": 1440, "height": 900})
            page.goto(f"{BASE}/v5#campaign", wait_until="domcontentloaded")
            page.locator("#commandTrigger").click()
            page.locator("#commandInput").fill("任务")
            assert page.locator('#commandResults [role="option"]').count() == 1
            assert (
                page.locator("#commandInput").get_attribute("aria-activedescendant")
                == "command-option-tasks"
            )
            page.keyboard.press("Enter")
            assert page.locator(".view.active").get_attribute("id") == "tasks"
            page.close()

            page = isolated_page(browser, viewport={"width": 400, "height": 800})
            page.goto(f"{BASE}/v5#campaign", wait_until="domcontentloaded")
            page.locator("#mobileMenu").click()
            assert page.locator("#mobileMenu").get_attribute("aria-expanded") == "true"
            page.keyboard.press("Escape")
            assert page.locator("#mobileMenu").get_attribute("aria-expanded") == "false"
            assert page.evaluate("document.activeElement.id") == "mobileMenu"
            page.close()
            assert_findings_with_isolated_api(browser)
            assert_research_creation_with_isolated_api(browser)
            assert_inspector_modal_with_isolated_api(browser)
            assert_task_controls_with_isolated_api(browser)
        finally:
            browser.close()

    print(json.dumps({
        "result": "passed",
        "views": len(VIEWS),
        "widths": WIDTHS,
        "themes": THEMES,
        "view_checks": checked,
        "checks": ["layout", "palette", "page errors", "command palette", "mobile navigation", "inspector modality", "isolated task controls", "isolated verified finding details", "isolated draft-to-run workflow"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
