"""Isolated bilingual rendering checks; no local service or external traffic."""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    requests, writes, errors = [], [], []
    def serve(route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/'):
            requests.append(path)
        if route.request.method != 'GET':
            writes.append(path)
            route.fulfill(status=405)
        elif path == '/v5':
            route.fulfill(path=str(ROOT / 'templates/v5.html'), content_type='text/html')
        elif path.startswith('/static/'):
            route.fulfill(path=str(ROOT / path.lstrip('/')))
        elif path == '/api/v1/engagements':
            route.fulfill(json=[])
        elif path == '/api/v1/task-center':
            route.fulfill(json=[{'id': 'task-fixture', 'engagement_id': 'project-fixture',
                                'status': 'paused', 'resume_supported': True,
                                'last_event': '未配置：原始事件必须保留'}])
        elif path == '/api/v1/capabilities':
            route.fulfill(json=[{'id': 'fixture-tool', 'detail': '已配置：原始能力描述',
                                'available': False, 'configured': True, 'sandbox_ready': False}])
        elif path == '/api/v1/findings':
            route.fulfill(json={'verified': [], 'candidates': []})
        elif path == '/api/v1/agent-audit/audits':
            route.fulfill(json=[])
        else:
            route.fulfill(status=503, json={'detail': 'Isolated fixture'})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.route('**/*', serve)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8012/v5#tasks')
        page.locator('#taskSearch').wait_for()
        page.locator('#taskSearch').fill('task-fixture')
        page.locator('#taskStatus').select_option('paused')
        count = len(requests)
        page.locator('#languageToggle').click()
        assert page.locator('#taskSearch').input_value() == 'task-fixture'
        assert page.locator('#taskStatus').input_value() == 'paused'
        assert page.locator('[data-run-action="resume"]').inner_text() == 'Resume'
        assert '未配置：原始事件必须保留' in page.locator('#tasksContent').inner_text()
        assert len(requests) == count, requests[count:]
        page.locator('[data-view="tools"]').click()
        assert 'Not installed' in page.locator('#toolsContent').inner_text()
        assert '已配置：原始能力描述' in page.locator('#toolsContent').inner_text()
        page.locator('#languageToggle').click()
        assert '未安装' in page.locator('#toolsContent').inner_text()
        assert '已配置：原始能力描述' in page.locator('#toolsContent').inner_text()
        for width in (390, 1280):
            for _ in range(2):
                page.set_viewport_size({'width': 1280, 'height': 900})
                page.locator('#themeToggle').click()
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not writes, writes
        assert not errors, errors
        browser.close()
    print('PASS: task filters, tool states, language roundtrip, raw data preservation, no language-triggered API calls, themes and widths')


if __name__ == '__main__':
    run()
