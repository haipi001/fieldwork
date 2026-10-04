"""Isolated V5 rule review and opt-in replay UI; no live service or scans."""
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'build' / 'v5-http-workflow-acceptance'


def run():
    writes, errors = [], []
    project = dict(id='project-fixture', name='Workflow fixture', mode='traditional',
                   normalized_target='https://example.test', status='draft', confirmed_at=None,
                   current_scope_snapshot_id='scope-1', scope={'allowed_targets': ['https://example.test'],
                   'allowed_actions': ['read', 'analyze'], 'denied_actions': ['destructive'],
                   'allow_authentication': False}, policy={'max_requests': 30, 'max_runtime_minutes': 30})
    job = dict(id='job-fixture', run_id='run-fixture', candidate_id='candidate-fixture',
               status='running', result={'requests_sent': 2, 'items': []}, cancel_requested=False)
    plan = dict(run_id='run-fixture', candidate_id='candidate-fixture', engagement_id=project['id'],
                title='Object read fixture', source_fingerprint='a' * 64, scope_snapshot_id='scope-2',
                business_rule={'access': 'owner_only', 'source': 'Business specification section 4'},
                remaining_requests=27, request_count=10, can_execute=True, blockers=[],
                requests=[{'role': 'owner', 'url': 'https://example.test/object', 'identity_id': 'owner-fixture'},
                          {'role': 'anonymous', 'url': 'https://example.test/object', 'identity_id': None}])
    stale = [False]
    started = [False]

    def serve(route):
        request, path = route.request, urlparse(route.request.url).path
        if request.method != 'GET':
            payload = request.post_data_json or {}
            writes.append((request.method, path, payload))
            if path.endswith('/http-read-rules'):
                assert payload['scope_snapshot_id'] == 'scope-1'
                assert payload['rules'][0]['access'] == 'allowlist'
                project['scope'].update(http_object_read_rules=payload['rules'], allow_authentication=payload['allow_authentication'])
                project['current_scope_snapshot_id'] = 'scope-2'
                route.fulfill(json=project)
            elif path.endswith('/confirm'):
                assert payload['scope_snapshot_id'] == 'scope-2'
                project.update(status='ready', confirmed_at='2026-10-04T12:00:00Z')
                route.fulfill(json=project)
            elif path.endswith('/execute'):
                assert payload == {'authorized': True, 'source_fingerprint': 'a' * 64}
                if stale[0]:
                    route.fulfill(status=409, json={'detail': '计划已变化，请重新检查'})
                else:
                    started[0] = True
                    route.fulfill(status=202, json=job)
            elif path.endswith('/cancel'):
                job.update(status='cancelled', cancel_requested=True)
                route.fulfill(json=job)
            else:
                raise AssertionError(f'Unexpected write: {path}')
        elif path == '/v5': route.fulfill(path=str(ROOT / 'templates/v5.html'), content_type='text/html')
        elif path.startswith('/static/'): route.fulfill(path=str(ROOT / path.lstrip('/')))
        elif path == '/api/v1/engagements': route.fulfill(json=[project])
        elif path == '/api/v1/engagements/project-fixture': route.fulfill(json=project)
        elif path == '/api/v1/task-center': route.fulfill(json=[{'id': 'run-fixture', 'engagement_id':project['id'], 'status':'completed'}])
        elif path == '/api/v1/findings': route.fulfill(json={'verified':[], 'candidates':[]})
        elif path.endswith('/candidate-workflow'): route.fulfill(json={'items':[{'candidate_id':'candidate-fixture', 'title':'Object read fixture',
              'draft':{'verification_method':'http_object_read'}, 'triage':{'lane':'verification_ready','blockers':[]}}],
              'next_cursor':None, 'execution_job':job if started[0] else None})
        elif path.endswith('/execution-plan'): route.fulfill(json=plan)
        elif path == '/api/v1/guided-research/job-fixture': route.fulfill(json=job)
        elif path in ('/api/v1/capabilities', '/api/v1/agent-audit/audits'): route.fulfill(json=[])
        else: route.fulfill(status=503, json={'detail':'Outside isolated fixture'})

    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.route('**/*', serve)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8012/v5#verification')
        page.locator('#workflowRun option[value="run-fixture"]').wait_for(state='attached')
        page.evaluate("window.FieldworkReviewScope('project-fixture')")
        page.locator('#ruleAdd').wait_for()
        assert not writes
        page.locator('#ruleAdd').click()
        assert page.locator('[data-field="access"]').input_value() == ''
        page.locator('[data-field="target"]').fill('https://example.test/object')
        page.locator('[data-field="access"]').select_option('allowlist')
        page.locator('[data-field="allowed_principals"]').fill('B\nC')
        page.locator('[data-field="source"]').fill('Business specification section 4')
        page.locator('#ruleAuthentication').check()
        assert page.locator('#scopeForm [type=submit]').is_disabled()
        OUTPUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUTPUT / 'draft-rules.png'))
        page.evaluate("document.querySelector('#languageToggle').click()")
        assert page.locator('[data-field="allowed_principals"]').input_value() == 'B\nC'
        assert page.locator('[data-field="source"]').input_value() == 'Business specification section 4'
        assert page.locator('#scopeForm [type=submit]').is_disabled()
        page.locator('#ruleSave').click()
        page.locator('#ruleSave').wait_for(state='visible')
        page.wait_for_function("document.querySelector('#ruleSave').disabled && !document.querySelector('#scopeForm [type=submit]').disabled")
        assert len(writes) == 1 and project['confirmed_at'] is None
        page.locator('#scopeAccepted').check()
        page.locator('#scopeForm [type=submit]').click()
        page.wait_for_function("!document.querySelector('#scopeDialog').open")
        assert len(writes) == 2
        page.locator('#workflowRun').select_option('run-fixture')
        page.locator('#workflowRead').click()
        page.locator('[data-workflow-plan]').click()
        page.locator('#workflowAuthorized').wait_for()
        page.locator('#workflowExecutionDialog').get_by_text('Business specification section 4', exact=True).wait_for()
        assert page.locator('#workflowExecute').is_disabled() and len(writes)==2
        page.screenshot(path=str(OUTPUT / 'replay-plan.png'))
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            for _ in range(2):
                page.evaluate("document.querySelector('#themeToggle').click()")
                assert page.evaluate('document.documentElement.scrollWidth-innerWidth') <= 1
                assert page.locator('#workflowExecutionDialog').evaluate('(el)=>el.scrollWidth-el.clientWidth') <= 1
        page.locator('#workflowAuthorized').check()
        page.locator('#workflowExecute').click()
        page.locator('#workflowExecutionDialog [data-workflow-cancel]').wait_for()
        assert len(writes)==3
        page.locator('#workflowExecutionDialog [data-workflow-close]').click()
        page.locator('#candidateWorkflow [data-workflow-cancel]').click()
        page.locator('#candidateWorkflow [data-workflow-recheck]').wait_for()
        assert len(writes)==4 and job['status']=='cancelled'
        page.reload()
        page.locator('#workflowRun option[value="run-fixture"]').wait_for(state='attached')
        page.locator('#workflowRun').select_option('run-fixture')
        page.locator('#workflowRead').click()
        page.locator('#candidateWorkflow [data-workflow-recheck]').wait_for()
        assert len(writes)==4
        page.locator('#candidateWorkflow [data-workflow-recheck]').click()
        page.locator('#workflowAuthorized').check()
        stale[0]=True
        page.locator('#workflowExecute').click()
        page.get_by_text('计划已变化，请重新检查',exact=True).wait_for()
        assert page.locator('#workflowExecute').is_disabled()
        assert not page.locator('#workflowAuthorized').is_checked()
        assert len(writes)==5
        page.locator('[data-workflow-close]').click()
        page.evaluate("window.FieldworkReviewScope('project-fixture')")
        page.get_by_text('This scope is frozen.',exact=False).wait_for()
        assert page.locator('[data-field="target"]').is_disabled()
        assert not errors, errors
        browser.close()
    print('PASS: draft rules, unsaved freeze gate, locale retention, reviewed execution, cancellation, stale-plan rejection, frozen scope, responsive themes')


if __name__=='__main__':
    run()
