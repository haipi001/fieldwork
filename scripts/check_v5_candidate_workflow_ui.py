"""Isolated triage UI: read-only preview and one deliberately rejected fixture POST."""
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    errors, writes, reads = [], [], []
    fail = [False]
    batch_submissions=[]
    def serve(route):
        request = route.request
        url = urlparse(request.url)
        if request.method=='POST' and url.path.endswith('/candidate-workflow-batch/execute'):
            batch_submissions.append(request.post_data_json);writes.append(url.path);route.fulfill(status=409,json={'detail':'Batch materials changed; review again'});return
        if request.method != 'GET':
            writes.append(url.path)
            route.fulfill(status=405)
        elif url.path == '/v5':
            route.fulfill(body=re.sub(r"\{\{ asset_url\('([^']+)'\) \}\}",r"/static/\1",(ROOT/'templates/v5.html').read_text()),content_type='text/html')
        elif url.path.startswith('/static/'):
            route.fulfill(path=str(ROOT / url.path.lstrip('/')))
        elif url.path.endswith('/candidate-workflow-batch/execution-plan'):
            if parse_qs(url.query).get('after_candidate_id')==['batch-cursor-1']:
                route.fulfill(json={'run_id':'run-fixture','source_fingerprint':'b'*64,
                    'items':[{'candidate_id':'next-page-candidate','title':'Next page fixture','can_execute':True,'blockers':[]}],
                    'selected_candidate_ids':['next-page-candidate'],'request_limit':10,'remaining_requests':20,
                    'remaining_candidates':0,'next_cursor':None,'can_execute':True,'truncated':False});return
            route.fulfill(json={'run_id':'run-fixture','source_fingerprint':'a'*64,
                'items':[{'candidate_id':'item-impact_review','title':'Fixture impact_review','can_execute':True,'blockers':[],
                          'scope_snapshot_id':'scope-fixture','business_rule':{'access':'owner_only','source':'Frozen test rule'},
                          'requests':[{'role':'owner','url':'https://example.test/object','identity_id':'fixture-owner'}]},
                         {'candidate_id':'blocked-shared','title':'Legitimate shared fixture','can_execute':False,'blockers':['business_access_permitted']}],
                'selected_candidate_ids':['item-impact_review'],'request_limit':10,'remaining_requests':20,'can_execute':True,'truncated':True,'remaining_candidates':1,'next_cursor':'batch-cursor-1'})
        elif url.path.endswith('/candidate-workflow'):
            reads.append(url.query)
            if fail[0]:
                route.fulfill(status=503)
                return
            more = bool(parse_qs(url.query))
            lanes = ['blocked'] if more else ['observation', 'verification_ready', 'impact_review']
            route.fulfill(json={'items': [dict(candidate_id=f'item-{lane}', title=f'Fixture {lane}',draft={'verification_method':'impact_review','reviewed_replay_available':True} if lane=='impact_review' else {}, triage={'lane':lane, 'blockers':['identities'] if lane=='blocked' else []}) for lane in lanes], 'next_cursor':None if more else 'cursor-1'})
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
        page.get_by_text('Fixture impact_review',exact=True).wait_for()
        assert page.locator('[data-workflow-plan="item-impact_review"]').count()==1
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
        page.get_by_role('button',name='Review replay batch',exact=True).click()
        batch=page.get_by_role('dialog').filter(has=page.get_by_role('heading',name='Review independent replay batch',exact=True))
        batch.get_by_role('button',name='Execute reviewed batch',exact=True).wait_for()
        assert batch.get_by_role('button',name='Execute reviewed batch',exact=True).is_disabled()
        assert batch.locator('[data-batch-authorized]').bounding_box()['width']<=24
        assert 'Legitimate shared fixture' in batch.inner_text() and 'Rule permits this read' in batch.inner_text()
        batch.get_by_text('Review requests and business rule',exact=True).click()
        assert 'https://example.test/object' in batch.inner_text() and 'scope-fixture' in batch.inner_text()
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth-innerWidth')<=1
        batch.locator('[data-batch-authorized]').check()
        assert batch.get_by_role('button',name='Execute reviewed batch',exact=True).is_enabled()
        batch.get_by_role('button',name='Review next candidate page',exact=True).click()
        batch.get_by_text('Next page fixture',exact=False).wait_for()
        assert not batch.locator('[data-batch-authorized]').is_checked()
        assert batch.get_by_role('button',name='Execute reviewed batch',exact=True).is_disabled()
        batch.get_by_role('button',name='Return to first candidates',exact=True).click()
        batch.get_by_text('Legitimate shared fixture',exact=False).wait_for()
        assert not batch.locator('[data-batch-authorized]').is_checked()
        batch.get_by_role('button',name='Cancel',exact=True).click()
        assert page.locator('#workflowRun').input_value()=='run-fixture'
        page.get_by_role('button',name='Review replay batch',exact=True).click()
        batch.get_by_role('button',name='Review next candidate page',exact=True).wait_for()
        batch.get_by_role('button',name='Review next candidate page',exact=True).click()
        batch.get_by_text('Next page fixture',exact=False).wait_for()
        batch.locator('[data-batch-authorized]').check()
        batch.get_by_role('button',name='Execute reviewed batch',exact=True).click()
        batch.get_by_text('Batch materials changed; review again',exact=True).wait_for()
        assert batch.get_by_role('button',name='Execute reviewed batch',exact=True).is_disabled()
        assert not batch.locator('[data-batch-authorized]').is_checked()
        batch.get_by_role('button',name='Cancel',exact=True).click()
        fail[0]=True
        page.locator('#workflowRead').click()
        page.get_by_text('Triage read failed. Existing entries are retained; retry.').wait_for()
        assert page.get_by_text('Fixture blocked',exact=True).count()==1
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            for _ in range(2):
                page.evaluate("document.querySelector('#themeToggle').click()")
                assert page.evaluate('document.documentElement.scrollWidth-innerWidth')<=1
        fail[0]=False
        # An old/malformed server ignores the explicit IDs and returns a foreign list.
        page.evaluate("()=>window.FieldworkCandidateWorkflow.selectRun({id:'run-fixture'},['chosen-new-candidate'])")
        page.get_by_text('Triage read failed. Existing entries are retained; retry.').wait_for()
        assert page.locator('[data-workflow-focused]').count()==1
        assert page.locator('[data-workflow-continue]').is_disabled()
        page.evaluate('()=>window.FieldworkCandidateWorkflow.reviewFocusedBatch()')
        assert page.get_by_role('dialog').count()==0
        page.locator('[data-workflow-clear-focus]').click()
        page.get_by_text('Fixture verification_ready',exact=True).wait_for()
        assert page.locator('[data-workflow-focused]').count()==0
        assert page.locator('[data-workflow-continue]').is_enabled()
        page.evaluate("()=>window.FieldworkCandidateWorkflow.selectRun({id:'run-fixture'},['chosen-new-candidate'])")
        page.get_by_text('Triage read failed. Existing entries are retained; retry.').wait_for()
        page.evaluate("()=>window.FieldworkCandidateWorkflow.update([],'another-project')")
        assert page.locator('[data-workflow-focused]').count()==0
        assert page.locator('#workflowRun').input_value()==''
        assert batch_submissions==[{'authorized':True,'source_fingerprint':'b'*64,'after_candidate_id':'batch-cursor-1'}]
        assert writes==['/api/v1/runs/run-fixture/candidate-workflow-batch/execute'] and not errors,(writes,errors)
        browser.close()
    print('PASS: explicit read-only triage, filters, pagination, language retention, failure retention, responsive themes')


if __name__=='__main__':
    run()
