"""Read-only browser smoke for the V5 Evolution control plane.

All requests are intercepted, including static assets. No server or real
research records are used.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


BASE = os.getenv("FIELDWORK_V5_BASE", "http://127.0.0.1:8012").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
parsed = urlparse(BASE)
if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("Evolution UI smoke requires a loopback HTTP server")


def run() -> None:
    writes: list[tuple[str, dict]] = []
    group_one, group_two = "group-one", "group-two"
    pop_current = {
        "id": "population-current", "campaign_id": "campaign-ui", "group_id": group_one,
        "generation": 1, "parent_population_id": "population-old", "current": True,
        "selection_count": 1,
        "variants": [
            {"id": "variant-current-a", "claim_node_id": "claim-one", "rank": 1,
             "score": 0.81, "selected": 1, "parent_variant_ids": ["variant-old-a"],
             "signals": {"support_count": 2, "counterevidence_count": 1}},
            {"id": "variant-current-b", "claim_node_id": "claim-two", "rank": 2,
             "score": 0.42, "selected": 0, "parent_variant_ids": ["variant-old-b"],
             "signals": {"support_count": 1, "counterevidence_count": 2}},
        ],
    }
    pop_old = {**pop_current, "id": "population-old", "generation": 0,
               "parent_population_id": None, "current": False,
               "variants": [{**item, "id": item["parent_variant_ids"][0], "parent_variant_ids": []}
                            for item in pop_current["variants"]]}
    groups = [{"id": group_one, "role": "specialist", "status": "active"},
              {"id": group_two, "role": "specialist", "status": "active"}]
    claims = [{"id": "claim-one", "node_type": "claim", "title": "A bounded supported idea",
               "status": "draft", "attributes": {"group_id": group_one}},
              {"id": "claim-two", "node_type": "claim", "title": "Alternative bounded idea",
               "status": "draft", "attributes": {"group_id": group_one}},
              {"id": "claim-source", "node_type": "claim", "title": "Cross-group source idea",
               "status": "draft", "attributes": {"group_id": group_two}}]
    tasks = [{"id": "evolver-ui-task", "role": "evolver", "status": "queued", "group_id": group_one,
              "context_capsule": {"evolution_population_id": pop_current["id"], "evolution_mode": "mutate"}}]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=os.getenv("FIELDWORK_CHROME_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def isolated_assets(route):
            path = urlparse(route.request.url).path
            if path == "/v5":
                route.fulfill(path=str(ROOT / "templates/v5.html"), content_type="text/html")
            elif path.startswith("/static/"):
                route.fulfill(path=str(ROOT / path.lstrip("/")))
            elif path == "/api/v1/findings":
                route.fulfill(json={"verified": [], "candidates": []})
            elif path in {"/api/v1/task-center", "/api/v1/capabilities", "/api/v1/agent-audit/audits"}:
                route.fulfill(json=[])
            else:
                route.fulfill(status=503, json={"detail": "Outside isolated fixture"})
        page.route("**/*", isolated_assets)
        page.route("**/api/v1/engagements?mode=traditional", lambda route: route.fulfill(json=[{
            "id": "eng-ui", "name": "Isolated Evolution UI", "mode": "traditional", "status": "ready",
            "normalized_target": "https://ui.example.test",
        }]))
        page.route("**/api/v1/engagements/eng-ui/campaigns", lambda route: route.fulfill(json=[{
            "id": "campaign-ui", "name": "Isolated research campaign", "status": "active",
        }]))
        page.route("**/api/v1/orchestration/groups?campaign_id=campaign-ui", lambda route: route.fulfill(json={"items": groups}))
        page.route("**/api/v1/evolution/populations?campaign_id=campaign-ui&limit=500",
                   lambda route: route.fulfill(json={"items": [pop_current, pop_old]}))
        page.route("**/api/v1/evolution/transfers?campaign_id=campaign-ui&limit=500",
                   lambda route: route.fulfill(json={"items": [{"id": "transfer-ui", "source_group_id": group_two,
                       "target_group_id": group_one, "source_claim_id": "claim-source", "current": True,
                       "capsule": {"key_idea": "Cross-group source idea"}}]}))
        page.route("**/api/v1/orchestration/tasks?campaign_id=campaign-ui&limit=500",
                   lambda route: route.fulfill(json={"items": [{"id": f"unrelated-{index}", "role": "critic"}
                       for index in range(500)], "page": {"next_cursor": "page-two", "has_more": True}}))
        page.route("**/api/v1/orchestration/tasks?campaign_id=campaign-ui&limit=500&cursor=page-two",
                   lambda route: route.fulfill(json={"items": [{"id": f"unrelated-{index}", "role": "critic"}
                       for index in range(500, 1000)], "page": {"next_cursor": "page-three", "has_more": True}}))
        page.route("**/api/v1/orchestration/tasks?campaign_id=campaign-ui&limit=500&cursor=page-three",
                   lambda route: route.fulfill(json={"items": tasks, "page": {"next_cursor": None, "has_more": False}}))

        def load_task_pages():
            for _ in range(2):
                with page.expect_response("**/orchestration/tasks?*&cursor=*"):
                    page.locator("[data-evolution-more-tasks]").click()
                page.wait_for_function("!document.querySelector('[data-evolution-more-tasks]') || !document.querySelector('[data-evolution-more-tasks]').disabled")
            assert page.locator("[data-evolution-more-tasks]").count() == 0
            page.get_by_text("任务列表已读取完毕，共 1001 项。").wait_for()
        page.route("**/api/v1/research/campaigns/campaign-ui/graph?limit=1000",
                   lambda route: route.fulfill(json={"nodes": claims, "edges": [], "page": {"has_more": False}}))
        page.route("**/api/v1/workers/status", lambda route: route.fulfill(json={
            "evolver": {"local_provider_ready": True, "queued": 1},
        }))

        def write(route) -> None:
            writes.append((route.request.url, route.request.post_data_json or {}))
            if route.request.url.endswith("/advance"):
                route.fulfill(json={"population_id": pop_current["id"], "created": 1,
                                    "task_ids": ["evolver-ui-task"]})
            elif route.request.url.endswith("/collect"):
                route.fulfill(json={**pop_current, "id": "population-next"})
            elif "/local/tick" in route.request.url:
                route.fulfill(json={"status": "idle", "completed": []})
            elif route.request.url.endswith("/populations"):
                route.fulfill(status=201, json={**pop_current, "id": "population-seeded"})
            else:
                route.fulfill(status=201, json={"id": "transfer-created"})

        page.route("**/api/v1/evolution/populations/population-current/advance", write)
        page.route("**/api/v1/evolution/populations/population-current/collect", write)
        page.route("**/api/v1/workers/local/tick?limit=2&population_id=population-current", write)
        page.route("**/api/v1/evolution/populations", write)
        page.route("**/api/v1/evolution/transfers", write)
        page.goto(f"{BASE}/v5#evolution", wait_until="domcontentloaded")
        page.get_by_text("当前快照").wait_for()
        assert page.locator("#evolutionContent").get_by_text("A bounded supported idea").count() > 0
        assert page.locator("#evolutionContent").get_by_text("0.810").count() == 1
        assert page.locator('[data-evolution-action="collect"]').is_disabled()
        load_task_pages()
        page.locator('#evolutionSeedForm input[name="claim"]').first.check()
        page.locator('#evolutionSeedForm [name="selection"]').fill('1')
        page.locator('#languageToggle').click()
        assert page.locator('#evolutionFreshness').text_content() == 'Current snapshot'
        assert page.locator('#evolutionSeedForm input[name="claim"]').first.is_checked()
        assert page.locator('#evolutionSeedForm [name="selection"]').input_value() == '1'
        assert page.evaluate("!/[\\u3400-\\u9fff]/.test(document.querySelector('#evolutionContent').innerText)")
        assert writes == []
        page.locator('#languageToggle').click()
        assert page.locator('#evolutionSeedForm input[name="claim"]').first.is_checked()
        page.locator('[data-evolution-population="population-old"]').click()
        assert page.locator('[data-evolution-action="advance"]').is_disabled()
        page.locator('[data-evolution-population="population-current"]').click()
        page.locator('[data-evolution-action="advance"]').click()
        assert page.locator("#evolutionActionDialog").evaluate("el => el.open")
        page.evaluate("document.querySelector('#languageToggle').click()")
        assert page.locator('#evolutionActionTitle').inner_text() == 'Create evolver tasks for this generation'
        assert 'group budgets' in page.locator('#evolutionActionSummary').inner_text()
        page.evaluate("document.querySelector('#languageToggle').click()")
        assert writes == []
        page.locator("#evolutionActionBack").click()
        assert writes == []
        page.locator('[data-evolution-action="advance"]').click()
        page.locator("#evolutionActionCommit").click()
        page.get_by_text("已创建 1 个任务").wait_for()
        assert len(writes) == 1 and writes[0][0].endswith("/population-current/advance")
        load_task_pages()
        page.locator('[data-evolution-action="tick"]').click()
        page.locator("#evolutionActionCommit").click()
        page.get_by_text("本代 Worker tick 已完成").wait_for()
        assert "population_id=population-current" in writes[-1][0]
        tasks[0].update(status="running", lease_expires_at="2000-01-01T00:00:00+00:00")
        page.locator("#evolutionRefresh").click()
        page.get_by_text("已读取当前 Campaign 的真实演化数据。").wait_for()
        load_task_pages()
        page.locator(".evolution-task-list .state.running").get_by_text("运行中", exact=True).wait_for()
        assert page.locator('[data-evolution-action="tick"]').is_enabled()
        page.locator('[data-evolution-action="tick"]').click()
        with page.expect_response("**/workers/local/tick?limit=2&population_id=population-current"):
            page.locator("#evolutionActionCommit").click()
        page.wait_for_function("!document.querySelector('#evolutionActionDialog').open && !document.querySelector('#evolutionSeedForm button').disabled")
        assert len(writes) == 3 and "population_id=population-current" in writes[-1][0]
        page.locator('#evolutionSeedForm input[name="claim"]').nth(0).check()
        page.locator('#evolutionSeedForm input[name="claim"]').nth(1).check()
        page.locator('#evolutionSeedForm button[type="submit"]').click()
        page.get_by_text("研究群体已建立；尚未启动模型任务").wait_for()
        assert len(writes[-1][1]["variants"]) == 2
        page.locator("#evolutionSourceGroup").select_option(group_two)
        page.locator('#evolutionTransferForm select[name="claim"]').select_option("claim-source")
        page.locator('#evolutionTransferForm button[type="submit"]').click()
        page.get_by_text("已建立目标组 Specialist 任务；尚未独立验证").wait_for()
        assert writes[-1][1]["source_claim_id"] == "claim-source"
        assert not errors, errors
        for width in (390, 1280):
            page.set_viewport_size({"width": width, "height": 850})
            overflow = page.evaluate("""() => ({width: innerWidth, scroll: document.documentElement.scrollWidth,
                elements: [...document.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                    .slice(0, 12).map(el => `${el.tagName}.${el.className || ''}#${el.id || ''}: ${Math.round(el.getBoundingClientRect().right)}`)})""")
            assert overflow["scroll"] <= width + 1, overflow
        screenshot = os.getenv("FIELDWORK_EVOLUTION_SCREENSHOT")
        if screenshot:
            path = Path(screenshot).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
        browser.close()
    print("Evolution UI smoke passed: real view mapping, stale gate, confirmed actions, scoped tick, running lease recovery, seed, transfer, responsive width")


if __name__ == "__main__":
    run()
