"""Eight-stage UI contract using isolated API fixtures, no model or target requests."""
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import isolated_page, BASE, CHROME

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=CHROME, headless=True)
    try:
        page = isolated_page(browser, viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        project = {'id': 'stage-project', 'name': '八阶段隔离验收', 'mode': 'traditional', 'status': 'ready', 'confirmed_at': '2026-10-14'}
        run = {'id': 'stage-run', 'engagement_id': project['id'], 'status': 'completed', 'completed_stages': 8}
        stages = [dict(id=id, status=status, summary='Isolated stage evidence') for id, status in zip(
            ['target', 'scope', 'surface', 'analysis', 'processing', 'verification', 'impact', 'report'],
            ['completed', 'completed', 'partial', 'failed', 'completed', 'human_review', 'not_applicable', 'completed'])]
        page.route('**/api/v1/engagements?mode=traditional', lambda r: r.fulfill(json=[project]))
        page.route('**/api/v1/task-center?mode=traditional', lambda r: r.fulfill(json={'items': [run]}))
        page.route('**/api/v1/runs/stage-run', lambda r: r.fulfill(json={**run, 'events': []}))
        page.route('**/api/v1/runs/stage-run/details', lambda r: r.fulfill(json={'stages': stages, 'test_items': [], 'artifacts': [], 'coverage': [], 'observations': []}))
        page.goto(f'{BASE}/v5#campaign')
        page.wait_for_function("document.querySelector('#homeMetric0')?.textContent==='4 / 8'")
        assert page.locator('#homeStages > div').count() == 8
        assert page.locator('#homeStages .done').count() == 4
        text = page.locator('#homeStages').inner_text()
        for expected in ['目标解析', '漏洞验证', '影响判断', '部分完成', '失败', '待复验', '不适用']:
            assert expected in text, (expected, text)
        for width in [400, 800, 1280]:
            page.set_viewport_size({'width': width, 'height': 900})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors, errors
        print('Passed: eight named stages, evidence-derived 4/8, partial/failed/review/not-applicable, three widths; no live requests')
    finally:
        browser.close()
