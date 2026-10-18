"""Isolated next-step routing: all HTTP requests are intercepted; no real execution."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import BASE, CHROME, isolated_page, assert_research_creation_with_isolated_api


def check_next_steps(browser):
    cases = [
        ("draft", None, [], [], "继续核对授权", "scopeDialog"),
        ("ready", None, [], [], "检查并开始研究", "executionDialog"),
        ("ready", "running", [], [], "查看当前运行", "tasks"),
        ("ready", "failed", [], [], "查看阻塞原因", "tasks"),
        ("ready", "paused", [], [], "继续处理暂停任务", "tasks"),
        ("ready", "completed", [{"id": "candidate-flow", "status": "candidate"}], [], "一键继续深挖", "verification"),
        ("ready", "completed", [], [{"id": "finding-flow", "status": "verified"}], "准备报告", "reports"),
        ("ready", "completed", [], [], "查看本次证据", "evidence"),
    ]
    for status, run_status, candidates, verified, label, destination in cases:
        page = isolated_page(browser, viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        project = {"id": "project-flow", "name": "隔离流程项目", "mode": "traditional", "status": status, "confirmed_at": "2026-10-13" if status == "ready" else None}
        page.route("**/api/v1/engagements?mode=traditional", lambda r: r.fulfill(json=[{"id": "other-project", "name": "另一个项目", "status": "ready"}, project]))
        page.route("**/api/v1/engagements/project-flow", lambda r: r.fulfill(json=project))
        page.route("**/api/v1/engagements/project-flow/execution-plan", lambda r: r.fulfill(json={"ready": False, "blockers": [{"message": "隔离预检"}]}))
        page.route("**/api/v1/task-center?mode=traditional", lambda r: r.fulfill(json=[{"id": "run-other", "engagement_id": "other-project", "status": "running"}, {"id": "run-flow", "engagement_id": project["id"], "status": run_status}] if run_status else []))
        page.route("**/api/v1/findings?mode=traditional", lambda r: r.fulfill(json={"candidates": [{**x, "engagement_id": project["id"]} for x in candidates], "verified": [{**x, "engagement_id": project["id"]} for x in verified]}))
        page.route("**/api/v1/findings/finding-flow/reports/generic_src/preview",lambda r:r.fulfill(json={"content":"Automatic verified report preview","adapter":"generic_src","completeness":{"ready":False,"missing_required":[],"missing_recommended":[]}}))
        page.goto(f"{BASE}/v5#campaign")
        page.locator("#homeProject").select_option("project-flow")
        page.locator("#homePlan").filter(has_text=label).wait_for()
        assert page.locator(".home-focus").bounding_box()["y"] < page.locator(".home-metrics").bounding_box()["y"]
        if label=="准备报告":
            page.locator("#languageToggle").click()
            page.locator("#homePlan").filter(has_text="Prepare report").wait_for()
            assert page.locator("#homeProject").input_value()=="project-flow"
            assert page.locator("#homeOverview > .home-focus").count()==1
            page.locator("#languageToggle").click()
            page.locator("#homePlan").filter(has_text=label).wait_for()
            output=Path(__file__).resolve().parents[1]/"build/acceptance/simplified-flow"
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/"home-1280.png"),animations="disabled")
            page.set_viewport_size({"width":400,"height":900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output/"home-400.png"),animations="disabled")
        page.locator("#homePlan").click()
        if destination.endswith("Dialog"):
            page.wait_for_function("id => document.getElementById(id).open", arg=destination)
        else:
            assert page.locator(".view.active").get_attribute("id") == destination
        if destination == "tasks":
            assert page.locator("#taskSearch").input_value() == "run-flow"
        elif destination == "reports":
            assert page.locator("#reportFinding").input_value() == "finding-flow"
            page.locator("#reportPreview").filter(has_text="Automatic verified report preview").wait_for()
            assert page.locator("#reportExport").is_disabled()
        elif destination == "verification":
            assert page.locator("#verificationCandidate").input_value() == "candidate-flow"
        elif destination == "evidence":
            assert page.locator("#evidence .run-surface-select").input_value() == "run-flow"
        assert not errors, errors
        page.close()


def check_evidence_handoff(browser):
    for items, candidates, verified, label in [
        ([{"id":"httpx","label":"HTTP 探测","status":"not_tested","result":"tool_not_ready"}], [], [], "检查并补齐执行条件"),
        ([{"id":"httpx","status":"completed"}], [{"id":"candidate-flow","status":"candidate"}], [], "继续独立复验"),
        ([{"id":"httpx","status":"completed"}], [], [{"id":"finding-flow","status":"verified"}], "准备报告"),
        ([{"id":"httpx","status":"completed"}], [], [], "返回项目总结"),
        ([], [], [], "返回项目总结"),
        ([{"id":"httpx","status":"completed"}], [], [{"id":"finding-flow","status":"verified"}], "复核历史证明"),
    ]:
        page=isolated_page(browser,viewport={"width":1280,"height":900})
        errors=[]; page.on("pageerror",lambda e:errors.append(str(e)))
        project={"id":"project-flow","name":"隔离证据项目","mode":"traditional","status":"ready","confirmed_at":"2026-10-14"}
        page.route("**/api/v1/engagements?mode=traditional",lambda r:r.fulfill(json=[project,{"id":"other-project","name":"Other","status":"ready"}]))
        page.route("**/api/v1/engagements/project-flow",lambda r:r.fulfill(json=project))
        page.route("**/api/v1/task-center?mode=traditional",lambda r:r.fulfill(json=[{"id":"run-other","engagement_id":"other-project","status":"completed"},{"id":"run-flow","engagement_id":"project-flow","status":"completed"}]))
        page.route("**/api/v1/runs/run-flow",lambda r:r.fulfill(json={"id":"run-flow","status":"completed","engagement_id":"project-flow"}))
        page.route("**/api/v1/runs/run-flow/details",lambda r:r.fulfill(json={"engagement":project,"test_items":items,"artifacts":[],"coverage":[],"observations":[],"proof_status":{"review_required":1,"items":[{"candidate_id":"candidate-history","finding_id":"finding-flow"}]} if label=="复核历史证明" else {}}))
        page.route("**/api/v1/findings?mode=traditional&run_id=run-flow",lambda r:r.fulfill(json={"candidates":candidates,"verified":verified}))
        page.route("**/api/v1/engagements/project-flow/execution-plan",lambda r:r.fulfill(json={"ready":False,"blockers":[{"message":"Isolated prerequisite blocker"}]}))
        page.route("**/api/v1/candidates/candidate-history",lambda r:r.fulfill(json={"candidate":{"id":"candidate-history","run_id":"run-flow","engagement_id":"project-flow","mode":"traditional","status":"verified","title":"Historical proof"},"evidence":[],"attempts":[],"verification_jobs":[]}))
        page.goto(f"{BASE}/v5#campaign")
        page.locator("#homeProject").select_option("project-flow")
        page.locator("#homeFocus h3").filter(has_text="隔离证据项目").wait_for()
        # Use the project Evidence tab for every outcome, including pending leads.
        page.locator('[data-home-view="evidence"]').click()
        page.locator(".evidence-next-action").filter(has_text=label).wait_for()
        assert page.locator('#workspace-evidence [name="project"]').input_value()=="project-flow"
        assert page.locator("#evidenceContent").bounding_box()["y"]<page.locator("#workspace-evidence").bounding_box()["y"]
        if items and items[0]["status"]=="not_tested":
            assert "tool_not_ready" in page.locator(".evidence-next-step").inner_text()
            page.locator(".evidence-next-action").click()
            page.wait_for_function("document.getElementById('executionDialog').open")
            page.locator("#executionPlan").filter(has_text="Isolated prerequisite blocker").wait_for()
            assert page.locator("#executionCommit").is_disabled()
            page.locator("#executionDialog").evaluate("el=>el.close()")
        if label=="复核历史证明":
            page.locator(".evidence-next-action").click()
            page.wait_for_function("document.getElementById('verificationCandidate').value==='candidate-history'")
            assert page.locator('#verificationCandidate').input_value()=='candidate-history'
            page.locator('#verificationDetail').filter(has_text='candidate-history').wait_for()
            page.locator('[data-view="evidence"]').click()
        if not items: assert "未记录执行清单" in page.locator(".evidence-next-step").inner_text()
        page.set_viewport_size({"width":400,"height":900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator('#workspace-evidence [name="project"]').select_option("")
        assert page.locator('#workspace-evidence [name="project"]').input_value()==""
        assert not errors,errors
        page.close()


def check_team_prerequisites(browser):
    cases = [(None, [], "新建研究项目", "researchDialog"),
             ("draft", [], "核对当前项目授权", "scopeDialog"),
             ("ready", [], "建立研究 Campaign", "researchCampaignDialog"),
             ("ready", [{"id": "campaign-flow", "status": "active"}], "检查并启动 Run", "executionDialog")]
    for status, campaigns, label, dialog in cases:
        page = isolated_page(browser, viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        project = {"id": "project-flow", "name": "隔离组队项目", "status": status, "mode": "traditional", "confirmed_at": "2026-10-14" if status=="ready" else None}
        writes=[]
        page.route("**/api/v1/engagements?mode=traditional", lambda r: r.fulfill(json=[project] if status else []))
        page.route("**/api/v1/engagements/project-flow", lambda r: r.fulfill(json=project))
        def campaign_route(route):
            if route.request.method=="POST":
                writes.append(route.request.post_data_json)
                campaigns.append({"id":"campaign-flow", "name":"隔离研究", "objective":"Review isolated recorded evidence", "status":"active"})
                route.fulfill(json=campaigns[-1])
            else:
                route.fulfill(json=campaigns)
        page.route("**/api/v1/engagements/project-flow/campaigns", campaign_route)
        page.route("**/api/v1/campaigns/campaign-flow", lambda r:r.fulfill(json={"hypotheses":[]}))
        page.route("**/api/v1/runs?mode=traditional", lambda r:r.fulfill(json=[]))
        page.route("**/api/v1/orchestration/groups?*", lambda r:r.fulfill(json={"items":[]}))
        page.route("**/api/v1/orchestration/tasks?*", lambda r:r.fulfill(json={"items":[]}))
        page.route("**/api/v1/orchestration/status", lambda r:r.fulfill(json={"runners":[]}))
        page.route("**/api/v1/engagements/project-flow/execution-plan", lambda r:r.fulfill(json={"ready":False,"blockers":[{"message":"隔离预检"}]}))
        page.goto(f"{BASE}/v5#agents")
        page.locator("#teamNextStep").filter(has_text=label).wait_for()
        assert not writes
        page.locator("#teamNextStep").click()
        page.wait_for_function("id=>document.getElementById(id).open",arg=dialog)
        if dialog=="researchCampaignDialog":
            page.locator("#researchCampaignName").fill("隔离研究")
            page.locator("#researchCampaignObjective").fill("Review isolated recorded evidence")
            page.locator('#researchCampaignForm [type=submit]').click()
            page.wait_for_function("!document.querySelector('#researchCampaignDialog').open && document.querySelector('#teamCampaign').value==='campaign-flow'")
            assert page.locator(".view.active").get_attribute("id")=="agents"
            assert page.locator("#teamEngagement").input_value()=="project-flow"
            assert len(writes)==1
        assert not errors,errors
        page.close()


def check_project_context(browser):
    page=isolated_page(browser,viewport={"width":1280,"height":900})
    projects=[{"id":"project-a","name":"项目甲","status":"ready","confirmed_at":"2026-10-14"},
              {"id":"project-b","name":"项目乙","status":"ready","confirmed_at":"2026-10-14"}]
    page.route("**/api/v1/engagements?mode=traditional",lambda r:r.fulfill(json=projects))
    page.route("**/api/v1/engagements/*/campaigns",lambda r:r.fulfill(json=[]))
    page.route("**/api/v1/runs?mode=traditional",lambda r:r.fulfill(json=[]))
    page.route("**/api/v1/runtime/config",lambda r:r.fulfill(json={"providers":[],"profiles":[]}))
    page.route("**/api/v1/runtime/routes?*",lambda r:r.fulfill(json={"items":[]}))
    page.route("**/api/v1/runtime/calls?*",lambda r:r.fulfill(json={"items":[]}))
    page.goto(f"{BASE}/v5#campaign")
    page.locator("#homeProject").select_option("project-b")
    page.locator('[data-view="agents"]').click()
    page.wait_for_function("document.querySelector('#teamEngagement')?.value==='project-b'")
    page.locator('#teamConfigureModel').click()
    page.wait_for_function("document.querySelector('#rcEngagement').value==='project-b'")
    page.locator('#rcPolicyForm').locator('..').locator('summary').click()
    page.locator('#rcEngagement').select_option('project-a')
    page.locator('[data-view="agents"]').click()
    page.wait_for_function("document.querySelector('#teamEngagement')?.value==='project-a'")
    page.locator('[data-view="campaign"]').click()
    page.wait_for_function("document.querySelector('#homeProject').value==='project-a'")
    page.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        try:
            assert_research_creation_with_isolated_api(browser)
            check_next_steps(browser)
            check_evidence_handoff(browser)
            check_team_prerequisites(browser)
            check_project_context(browser)
        finally:
            browser.close()
    print("Passed: automatic draft/scope/preflight/run handoff 8 project next-step states, 6 evidence handoffs and 4 team prerequisite handoffs; no live writes")
