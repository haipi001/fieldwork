"""Isolated operational triage UI acceptance; no real service or writes."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    errors, writes, reads = [], [], []
    fail = [False]
    def serve(route):
        request = route.request
        url = urlparse(request.url)
        if request.method != 'GET':
            writes.append(url.path)
            route.fulfill(status=405)
        elif url.path == '/v5':
            route.fulfill(path=str(ROOT / 'templates/v5.html'), content_type='text/html')
        elif url.path.startswith('/static/'):
            route.fulfill(path=str(ROOT / url.path.lstrip('/')))
        elif url.path.endswith('/candidate-workflow'):
            reads.append(url.query)
            if fail[0]:
                route.fulfill(status=503)
                return
            more = bool(parse_qs(url.query))
            lanes = ['blocked'] if more else ['observation', 'verification_ready']
            route.fulfill(json={'items': [dict(candidate_id=f'item-{lane}', title=f'Fixture {lane}', triage={'lane':lane, 'blockers':['identities'] if lane=='blocked' else []}) for lane in lanes], 'next_cursor':None if more else 'cursor-1'})
        elif url.path == '/api/v1/task-center':
            route.fulfill(json=[{'id':'run-fixture', 'status':'completed'}])
        elif url.path == '/api/v1/findings':
            route.fulfill(json={'verified':[], 'candidates':[]})
        elif url.path in ('/api/v1/engagements', '/api/v1/capabilities', '/api/v1/agent-audit/audits'):
            route.fulfill(json=[])
        else:
            route.fulfill(status=503, json={'detail':'Outside isolated fixture'})
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.route('**/*',serve)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8012/v5#verification')
        page.locator('#workflowRun option[value="run-fixture"]').wait_for(state='attached')
        assert not reads
        page.locator('#workflowRun').select_option('run-fixture')
        page.locator('#workflowRead').click()
        page.get_by_text('Fixture verification_ready',exact=True).wait_for()
        assert page.get_by_text('Fixture observation',exact=True).count()==0
        page.locator('#workflowFilter').select_option('all')
        page.get_by_text('Fixture observation',exact=True).wait_for()
        page.locator('#workflowMore').click()
        page.get_by_text('Fixture blocked',exact=True).wait_for()
        assert len(reads)==2
        page.evaluate("document.querySelector('#languageToggle').click()")
        assert page.locator('#workflowRun').input_value()=='run-fixture'
        assert page.locator('#workflowFilter').input_value()=='all'
        assert 'Materials ready' in page.locator('#candidateWorkflow').inner_text()
        assert len(reads)==2
        fail[0]=True
        page.locator('#workflowRead').click()
        page.get_by_text('Triage read failed. Existing entries are retained; retry.').wait_for()
        assert page.get_by_text('Fixture blocked',exact=True).count()==1
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            for _ in range(2):
                page.evaluate("document.querySelector('#themeToggle').click()")
                assert page.evaluate('document.documentElement.scrollWidth-innerWidth')<=1
        assert not writes and not errors, (writes,errors)
        browser.close()
    print('PASS: explicit read-only triage, filters, pagination, language retention, failure retention, responsive themes')


if __name__=='__main__':
    run()
