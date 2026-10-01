"""Capture privacy-safe Fieldwork V5 UI examples for the public site.

The images are the real V5 HTML/CSS shell with isolated example content. No
application database, authorization token, or real target is read.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "website" / "dist" / "images"
ORIGIN = "https://fieldwork-preview.invalid"


def fulfill_local(route):
    path = route.request.url.split(ORIGIN, 1)[-1].split("?", 1)[0]
    if path == "/v5":
        route.fulfill(path=str(ROOT / "templates" / "v5.html"), content_type="text/html")
    elif path.startswith("/static/"):
        local = ROOT / path.lstrip("/")
        if local.is_file() and local.suffix in {".css", ".js", ".svg", ".png"}:
            # Keep the unmodified V5 shell stable; real app scripts require a
            # live authenticated backend and would replace the example state.
            if local.suffix == ".js":
                route.fulfill(body="", content_type="application/javascript")
            else:
                route.fulfill(path=str(local))
        else:
            route.fulfill(status=404)
    elif path.startswith("/api/"):
        route.fulfill(status=404, json={"detail": "isolated preview"})
    else:
        route.fulfill(status=404)


def prepare(page):
    page.route(f"{ORIGIN}/**", fulfill_local)
    page.goto(f"{ORIGIN}/v5", wait_until="domcontentloaded")
    page.wait_for_timeout(850)
    page.evaluate("""() => {
      localStorage.clear();
      document.documentElement.dataset.theme = 'dark';
      document.querySelector('#connectionBanner').hidden = true;
      document.querySelector('#sidebarStatus').innerHTML = '<i class="status-dot"></i><div><b>隔离界面预览</b><small>示例内容 · 无真实项目</small></div>';
      document.querySelector('#runtimeTopStatus').textContent = 'LOCAL PREVIEW';
      document.querySelector('#lastUpdated').textContent = '界面示意 · 示例数据';
      document.querySelector('#domainSelect').value = 'agent_audit';
      document.querySelector('#workspaceMode').textContent = 'AI Agent Security';
      document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    }""")


with sync_playwright() as p:
    OUT.mkdir(parents=True, exist_ok=True)
    browser = p.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)
    prepare(page)
    page.evaluate("""() => {
      document.querySelector('#campaign').classList.add('active');
      document.querySelector('#currentViewName').textContent = 'Campaigns';
      document.querySelector('#focusHero').innerHTML = '<div style="padding:28px"><small>CURRENT FOCUS · ISOLATED EXAMPLE</small><h2 style="font-size:30px;margin:16px 0">AI Agent 行为边界研究</h2><p>Scope 已冻结 · 等待人工选择下一步</p></div>';
      [['metricEngagements','03'],['metricTasks','01'],['metricCandidates','02'],['metricVerified','00'],['trustVerified','00'],['trustCandidates','02'],['trustRatio','待复核']].forEach(([id,value]) => document.getElementById(id).textContent=value);
      document.querySelector('#metricEngagementsSub').textContent='隔离示例';
      document.querySelector('#metricTasksSub').textContent='隔离示例';
      document.querySelector('#engagementList').innerHTML='<div class="data-row" style="padding:18px">AI Agent 行为边界研究 <small>· Scope 已确认</small></div><div class="data-row" style="padding:18px">授权接口研究 <small>· 草稿</small></div>';
      document.querySelector('#attentionList').innerHTML='<div class="data-row" style="padding:18px">2 条候选需要独立复验</div><div class="data-row" style="padding:18px">1 项执行计划等待确认</div>';
      document.querySelector('#taskList').innerHTML='<div class="data-row" style="padding:18px">证据归集 <small>· 已暂停</small></div>';
      document.querySelector('#attentionCount').textContent='03';
      document.querySelectorAll('.loading-state').forEach(el=>el.classList.remove('loading-state'));
      document.querySelector('#connectionBanner').hidden=true;
    }""")
    page.screenshot(path=str(OUT / "fieldwork-v5-overview.png"), animations="disabled")
    page.evaluate("""() => {
      document.querySelector('#campaign').classList.remove('active');
      document.querySelector('#graph').classList.add('active');
      document.querySelector('[data-view="campaign"]').classList.remove('active');
      document.querySelector('[data-view="graph"]').classList.add('active');
      document.querySelector('#currentViewName').textContent='Research Graph';
      document.querySelector('#graphCounts').textContent='界面示意 · 4 个节点 · 3 条关系';
      document.querySelector('#graphCanvas').innerHTML='<div style="padding:28px;min-height:440px;display:grid;place-items:center"><svg viewBox="0 0 700 360" width="100%" role="img" aria-label="隔离示例研究关系图"><g stroke="#5e7287" stroke-width="2"><line x1="130" y1="180" x2="330" y2="85"/><line x1="130" y1="180" x2="340" y2="260"/><line x1="330" y1="85" x2="560" y2="180"/></g><g fill="#1c2c3b" stroke="#8fb5d5" stroke-width="2"><rect x="40" y="146" width="180" height="68" rx="8"/><rect x="250" y="50" width="180" height="68" rx="8"/><rect x="250" y="226" width="180" height="68" rx="8"/><rect x="470" y="146" width="180" height="68" rx="8"/></g><g fill="#e9f0f8" font-size="15" font-family="sans-serif" text-anchor="middle"><text x="130" y="186">授权范围</text><text x="340" y="90">研究假设</text><text x="340" y="266">观察证据</text><text x="560" y="186">待验证候选</text></g></svg></div>';
      document.querySelector('#graphInspectorTitle').textContent='研究假设';
      document.querySelector('#graphInspectorBody').textContent='示例关系只展示来源与状态；候选不会自动成为可信结果。';
      document.querySelector('#connectionBanner').hidden=true;
    }""")
    page.screenshot(path=str(OUT / "fieldwork-v5-graph.png"), animations="disabled")
    browser.close()
