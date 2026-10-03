"""Serverless browser acceptance: actual V5 assets, intercepted fixture reads only."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8012"


def run():
    nodes = [{"id": f"node-{index:04d}", "campaign_id": "camp-a", "title": f"Research node {index}",
              "node_type": "claim", "status": "draft", "attributes": {}} for index in range(1001)]
    edges = [{"id": f"edge-{index:04d}", "campaign_id": "camp-a", "source_id": "node-1000",
              "target_id": f"node-{index:04d}", "relation_type": "contradicts"} for index in range(1000)]
    edges += [{"id": f"edge-{index+1000:04d}", "campaign_id": "camp-a", "source_id": f"node-{index:04d}",
               "target_id": "node-0999", "relation_type": "supports"} for index in range(201)]
    failures, calls, unexpected = {200: 1}, [], []

    def route_request(route):
        request = route.request
        url = urlparse(request.url)
        if request.method != "GET":
            unexpected.append(f"write: {request.method} {url.path}")
            route.abort()
            return
        if url.path == "/v5":
            route.fulfill(path=str(ROOT / "templates/v5.html"), content_type="text/html")
        elif url.path.startswith("/static/"):
            path = (ROOT / url.path.lstrip("/")).resolve()
            if not path.is_relative_to(ROOT / "static") or not path.is_file():
                route.fulfill(status=404)
            else:
                route.fulfill(path=str(path))
        elif url.path == "/api/v1/engagements":
            route.fulfill(json=[{"id": "eg-a", "name": "Graph pagination fixture", "mode": "traditional", "status": "ready"}])
        elif url.path == "/api/v1/engagements/eg-a/campaigns":
            route.fulfill(json=[{"id": "camp-a", "name": "Graph fixture"}])
        elif url.path == "/api/v1/campaigns/camp-a":
            route.fulfill(json={"id": "camp-a", "engagement_id": "eg-a", "hypotheses": [], "candidate_links": []})
        elif url.path == "/api/v1/research/campaigns/camp-a/graph/page":
            offset = int(parse_qs(url.query).get("cursor", ["0"])[0])
            calls.append(offset)
            if failures.get(offset, 0):
                failures[offset] -= 1
                route.fulfill(status=503, json={"detail": "fixture transient failure"})
                return
            more = offset + 200 < max(len(nodes), len(edges))
            route.fulfill(json={"campaign_id": "camp-a", "nodes": nodes[offset:offset+200],
                                "edges": edges[offset:offset+200],
                                "page": {"has_more": more, "next_cursor": str(offset+200) if more else None}})
        elif url.path.endswith("/asset-graph"):
            route.fulfill(json={"nodes": [], "edges": []})
        elif url.path == "/api/v1/findings":
            route.fulfill(json={"verified": [], "candidates": []})
        elif url.path in {"/api/v1/task-center", "/api/v1/agent-audit/audits", "/api/v1/capabilities"}:
            route.fulfill(json=[])
        elif url.path.startswith("/api/v1/"):
            route.fulfill(status=503, json={"detail": "Outside graph fixture"})
        else:
            unexpected.append(request.url)
            route.abort()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", route_request)
        page.goto(BASE + "/v5#graph")
        page.wait_for_function("document.querySelector('#graphCampaign').value === 'eg-a'")
        page.locator("#graphMode").select_option("research")
        try:
            page.locator("#graphLoadMore").wait_for(timeout=5000)
        except Exception:
            print({"errors": errors, "calls": calls, "graph": page.locator('#graph').inner_text()})
            raise
        assert page.locator("#graphCanvas .asset-node").count() == 202
        before_language = len(calls)
        page.locator('#languageToggle').click()
        assert page.locator("#graphCanvas .asset-node").count() == 202
        assert page.locator('#graphLoadMore').inner_text() == 'Load more graph data'
        page.locator('#languageToggle').click()
        assert len(calls) == before_language
        assert page.locator('#graphCanvas path[data-source="node-1000"]').count() == 0
        page.locator("#graphLoadMore").click()
        page.wait_for_function("document.querySelector('#graphCounts').textContent.includes('读取失败：503')")
        assert page.locator("#graphCanvas .asset-node").count() == 202
        for offset in range(200, 1201, 200):
            with page.expect_response(f"**/graph/page?cursor={offset}"):
                page.locator("#graphLoadMore").click()
            page.wait_for_function("!document.querySelector('#graphLoadMore') || !document.querySelector('#graphLoadMore').disabled")
        assert page.locator("#graphLoadMore").count() == 0
        assert page.locator("#graphCanvas .asset-node").count() == 1003
        assert page.locator('#graphCanvas path[data-source="node-1000"]').count() == 1000
        assert page.locator("#graphCanvas path").count() == 2203
        page.locator('.nav-item[data-view="campaign"]').click()
        page.locator("#homeGraphMode").select_option("research")
        page.locator("#homeGraphMore").wait_for()
        for offset in range(200, 1201, 200):
            with page.expect_response(f"**/graph/page?cursor={offset}"):
                page.locator("#homeGraphMore").click()
            page.wait_for_function("!document.querySelector('#homeGraphMore') || !document.querySelector('#homeGraphMore').disabled")
        assert page.locator("#homeGraphMore").count() == 0
        assert page.locator("#homeGraph .asset-node").count() == 1003
        assert page.locator("#homeGraph path").count() == 2203
        assert not errors, errors
        assert not unexpected, unexpected
        browser.close()
    print("Graph pagination passed: 1001 nodes, 1201 relations, delayed endpoints, retry, main/home controls; no writes or network")


if __name__ == "__main__":
    run()
