"""Populated and failed detail views: language changes never call real APIs."""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def run():
    errors, requests, writes = [], [], []
    failures = set()
    project = {'id': 'project-ui', 'name': '原始项目名称', 'mode': 'traditional', 'status': 'ready', 'normalized_target': 'https://authorized.example.test'}
    candidate = {'id': 'candidate-ui', 'title': '原始候选说明', 'status': 'candidate', 'run_id': 'run-ui', 'evidence_ids': ['evidence-ui']}
    finding = {'id': 'finding-ui', 'title': '原始发现说明', 'status': 'verified', 'mode': 'traditional', 'engagement_id': 'project-ui', 'severity': 'high', 'evidence_ids': ['evidence-ui'], 'evidence': [{'id': 'evidence-ui', 'summary': '原始证据说明'}]}
    tasks = [{'id': 'run-ui', 'engagement_id': 'project-ui', 'status': 'paused', 'resume_supported': True}, {'id': 'run-new', 'engagement_id': 'project-ui', 'status': 'completed'}]

    def serve(route):
        request, path = route.request, urlparse(route.request.url).path
        if path.startswith('/api/'):
            requests.append(path)
        if request.method != 'GET':
            writes.append(path)
            route.fulfill(status=405)
            return
        if path == '/v5':
            route.fulfill(path=str(ROOT / 'templates/v5.html'), content_type='text/html')
            return
        if path.startswith('/static/'):
            route.fulfill(path=str(ROOT / path.lstrip('/')))
            return
        if path in failures:
            route.fulfill(status=503, json={'detail': 'Isolated unavailable endpoint'})
            return
        data = {
            '/api/v1/engagements': [project],
            '/api/v1/task-center': tasks,
            '/api/v1/findings': {'verified': [finding], 'candidates': [candidate]},
            '/api/v1/findings/finding-ui': finding,
            '/api/v1/findings/finding-ui/lifecycle': {'finding_id': 'finding-ui', 'engagement_id': 'project-ui', 'last_seen_run_id': 'run-ui', 'status': 'open', 'retests': []},
            '/api/v1/findings/finding-ui/proof-capsule': {'finding_id': 'finding-ui', 'sha256': 'fixture-digest', 'replay': {'kind': 'fixture'}, 'portability_status': 'portable'},
            '/api/v1/candidates/candidate-ui': {'candidate': candidate, 'attempts': [{'id': 'attempt-ui', 'status': 'machine_receipt'}], 'verification_jobs': [{'id': 'job-ui', 'status': 'succeeded'}], 'evidence': [{'id': 'evidence-ui'}]},
            '/api/v1/verification/receipts': [{'id': 'receipt-ui', 'claim_node_id': 'claim-ui', 'integrity': {'valid': True, 'current_inputs_match': True, 'promotion_eligible': True}, 'result': {'status': 'verified'}}],
            '/api/v1/runtime/readiness': {'ready': True},
            '/api/v1/capabilities': [],
            '/api/v1/runtime/config': {'providers': [{'id': 'provider-ui', 'name': 'Fixture provider', 'kind': 'local', 'location': 'local', 'model': 'fixture', 'last_health': 'ready', 'secret_configured': False}], 'profiles': [{'id': 'profile-ui', 'name': 'Fixture profile', 'mode': 'local', 'config': {'allow_fallback': False}}]},
            '/api/v1/runtime/usage': {'total': {'calls': 3}, 'items': []},
            '/api/v1/traditional/provider': {'configured': True, 'model': 'fixture', 'base_url': 'https://model.example.test/v1', 'has_api_key': False},
            '/api/v1/orchestration/status': {'agents': [{'id': 'task-ui', 'status': 'queued'}], 'runners': [{'id': 'runner-ui', 'status': 'ready'}], 'groups': []},
            '/api/v1/agent-audit/audits': [{'id': 'audit-ui', 'name': '原始审计名称', 'monitor_status': 'stopped', 'collector_kind': 'desktop'}],
            '/api/v1/agent-audit/audits/audit-ui': {'id': 'audit-ui', 'monitor': {'status': 'stopped'}, 'candidates': [], 'findings': []},
            '/api/v1/engagements/project-ui/campaigns': [{'id': 'campaign-ui', 'name': 'Fixture campaign'}],
            '/api/v1/campaigns/campaign-ui': {'hypotheses': [{'id': 'hypothesis-ui', 'statement': '原始假设说明', 'status': 'new'}]},
            '/api/v1/engagements/project-ui/asset-graph': {'nodes': [], 'edges': []},
        }.get(path)
        if data is None:
            route.fulfill(status=503, json={'detail': 'Outside isolated fixture'})
        else:
            route.fulfill(json=data)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.route('**/*', serve)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto('http://127.0.0.1:8012/v5#runtime')
        page.locator('#runtimeRegistry').get_by_text('Fixture profile', exact=True).wait_for()

        def language():
            count = len(requests)
            page.evaluate("document.querySelector('#languageToggle').click()")
            page.wait_for_timeout(60)
            assert len(requests) == count, requests[count:]

        def view(name):
            page.evaluate("name => document.querySelector(`[data-view='${name}']`).click()", name)

        page.locator('#providerConfig summary').click()
        page.locator('#providerModel').fill('user-edited-model')
        page.locator('#providerKey').fill('fixture-only-not-a-secret')
        page.locator('#providerConfirm').check()
        language()
        assert page.locator('#providerModel').input_value() == 'user-edited-model'
        assert page.locator('#providerKey').input_value() == 'fixture-only-not-a-secret'
        assert page.locator('#providerConfirm').is_checked()
        assert 'Configured' in page.locator('#providerStatus').inner_text()
        assert 'Profiles and providers' in page.locator('#runtime').inner_text()

        view('verification')
        page.locator('#verificationCandidate').select_option('candidate-ui')
        page.locator('#verificationDetail').get_by_text('attempt-ui', exact=True).wait_for()
        language()
        assert page.locator('#verificationCandidate').input_value() == 'candidate-ui'
        assert '当前候选' not in page.locator('#verificationDetail').inner_text()  # Actual records remain present.
        assert 'attempt-ui' in page.locator('#verificationDetail').inner_text()
        language()
        assert 'Verification Jobs' in page.locator('#verificationDetail').inner_text()
        failures.add('/api/v1/candidates/candidate-ui')
        page.locator('#verificationDetailRefresh').click()
        page.locator('#verificationDetail .unavailable').wait_for()
        language()
        assert '读取失败' in page.locator('#verificationDetail').inner_text()
        language()
        assert 'Could not load' in page.locator('#verificationDetail').inner_text()

        view('findings')
        page.locator('[data-finding-open="finding-ui"]').click()
        page.locator('[data-finding-proof="finding-ui"]').wait_for()
        page.locator('[data-finding-proof="finding-ui"]').click()
        page.get_by_text('SHA-256: fixture-digest', exact=True).wait_for()
        language()
        assert 'fixture-digest' in page.locator('#findingProofStatus').inner_text()
        assert '原始发现说明' in page.locator('#findingDetail').inner_text()
        language()
        assert 'Historical' not in page.locator('#findingProofStatus').inner_text()  # Uses the precise backend-checked claim.
        assert 'Backend read and checked' in page.locator('#findingProofStatus').inner_text()
        page.locator('[data-finding-retest="finding-ui"]').click()
        page.locator('#findingRetestRun').select_option('run-new')
        page.locator('#findingRetestNote').fill('原始复测草稿')
        language()
        assert page.locator('#findingRetestRun').input_value() == 'run-new'
        assert page.locator('#findingRetestNote').input_value() == '原始复测草稿'
        assert page.locator('#findingRetestDialog').evaluate('node => node.open')
        page.keyboard.press('Escape')

        view('tasks')
        page.locator('[data-run-action="resume"]').click()
        language()
        assert page.locator('#runControlCommit').inner_text() == 'Confirm resume'
        assert 'Resume execution' in page.locator('#runControlSummary').inner_text()
        page.keyboard.press('Escape')

        original = ['原始项目名称', '原始候选说明', '原始发现说明', '原始证据说明', '原始审计名称', '原始假设说明']
        for name in ('runtime', 'verification', 'findings', 'agents', 'runners', 'incidents', 'hypotheses', 'events', 'evidence'):
            view(name)
            page.wait_for_timeout(100)
            body = page.locator('.view.active').inner_text()
            for value in original:
                body = body.replace(value, '')
            assert not any('\u3400' <= char <= '\u9fff' for char in body), (name, body)
            for width in (390, 1280):
                page.set_viewport_size({'width': width, 'height': 1000})
                for theme in ('dark', 'light'):
                    page.evaluate("theme => document.documentElement.dataset.theme=theme", theme)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (name, width, theme)
        assert not writes, writes
        assert not errors, errors
        browser.close()
    print('PASS: populated verification/runtime/findings, proof retention, error retention, modal drafts, original evidence, no language-triggered requests or writes')


if __name__ == '__main__':
    run()
