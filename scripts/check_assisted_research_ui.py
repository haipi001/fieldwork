"""Only target entry, explicit authority, automatic AI selection and result update.

All requests use isolated browser fixtures. No actual target or model is contacted.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import isolated_page, BASE, CHROME


def run():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=CHROME,headless=True)
        try:
            page=isolated_page(browser,viewport={'width':1280,'height':900})
            state={'created':False,'confirmed':False,'started':False,'ended':False}
            writes=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            project={'id':'assisted-project','name':'Research · automatic.example.test','mode':'traditional',
                     'normalized_target':'https://automatic.example.test','current_scope_snapshot_id':'scope-auto',
                     'current_policy_id':'policy-auto','scope':{'allowed_targets':['https://automatic.example.test'],
                     'allowed_actions':['read','analyze'],'denied_actions':['destructive']},
                     'policy':{'max_requests':100,'max_runtime_minutes':30}}
            execution={'execution_mode':'real','scan_profile':'quick','source_path':None,'fork_rpc_url':None,
                       'include_native_agent':True,'native_runtime_profile_id':'automatic-ai'}
            def detail(route):
                route.fulfill(json={**project,'status':'ready' if state['confirmed'] else 'draft','confirmed_at':'2026-10-14' if state['confirmed'] else None})
            def projects(route):
                if route.request.method=='POST':
                    body=route.request.post_data_json
                    assert body['target']=='https://automatic.example.test'
                    assert body['name'] is None and body['scope']=={}
                    state['created']=True;writes.append('create');detail(route)
                else:
                    route.fulfill(json=[{**project,'status':'ready' if state['confirmed'] else 'draft'}] if state['created'] else [])
            def confirm(route):
                assert route.request.post_data_json['scope_snapshot_id']=='scope-auto'
                state['confirmed']=True;writes.append('confirm');detail(route)
            def preparation(route):
                route.fulfill(json={'execution':execution,'ai':{'enabled':True,'profile_id':'automatic-ai','name':'Already connected local AI'},
                    'plan_hash':'a'*64,'auto_start_ready':True,'missing':[],
                    'plan':{'ready':state['confirmed'],'tools':[{'id':'native-agent','ready':True}],
                            'warnings':[],'budget':{'requests':100,'runtime_minutes':30}}})
            def start(route):
                body=route.request.post_data_json
                assert state['confirmed'] and body['assisted_plan_hash']=='a'*64
                assert body['include_native_agent'] and body['native_runtime_profile_id']=='automatic-ai'
                state['started']=True;writes.append('start');route.fulfill(status=202,json={'id':'assisted-run','status':'queued'})
            page.route('**/api/v1/engagements?mode=traditional',projects)
            page.route('**/api/v1/engagements',projects)
            page.route('**/api/v1/engagements/assisted-project',detail)
            page.route('**/api/v1/engagements/assisted-project/confirm',confirm)
            page.route('**/api/v1/engagements/assisted-project/assisted-plan*',preparation)
            page.route('**/api/v1/engagements/assisted-project/start',start)
            page.route('**/api/v1/task-center?mode=traditional',lambda r:r.fulfill(json=[{'id':'assisted-run','engagement_id':project['id'],
                'status':'completed' if state['ended'] else 'queued','completed_stages':8 if state['ended'] else 0}] if state['started'] else []))
            page.route('**/api/v1/findings?*',lambda r:r.fulfill(json={'verified':[],'candidates':[{'id':'lead-auto',
                'engagement_id':project['id'],'title':'Recorded AI lead requires verification','status':'candidate'}] if state['ended'] else []}))
            page.route('**/api/v1/runs/assisted-run/summary-report',lambda r:r.fulfill(json={'run_id':'assisted-run','content':'Automatically compiled coverage and evidence summary'}))
            page.route('**/api/v1/runs/assisted-run',lambda r:r.fulfill(json={'id':'assisted-run','events':[]}))
            page.route('**/api/v1/runs/assisted-run/details',lambda r:r.fulfill(json={'artifacts':[{'id':'artifact-auto'}],
                'test_items':[{'id':'native-agent','label':'Local AI analysis','status':'completed','result':'One unverified lead recorded'},
                              {'id':'not-installed','label':'Unavailable tool','status':'not_tested','result':'Not installed'}],
                'observations':[],'coverage':[]}))
            def ended_stream(route):
                state['ended']=True
                route.fulfill(content_type='text/event-stream',body='data: {"id":1,"run_id":"assisted-run","kind":"run.completed","message":"Fixture complete"}\n\n')
            page.route('**/api/v1/runs/assisted-run/events',ended_stream)
            page.goto(BASE+'/v5#campaign')
            page.locator('#newResearch').click()
            assert not page.locator('#researchAdvanced').evaluate('(node)=>node.open')
            output=Path(__file__).resolve().parents[1]/'build/acceptance/assisted-research'
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'target-only.png'),animations='disabled')
            page.locator('#researchTarget').fill('https://automatic.example.test')
            page.locator('#researchForm [type=submit]').click()
            page.locator('#scopePrepared').get_by_text('Already connected local AI').wait_for()
            assert writes==['create']
            assert not page.locator('#scopeForm [type=submit]').is_disabled()
            page.locator('#scopeAccepted').check()
            page.locator('#scopeForm [type=submit]').click()
            page.locator('#homeOutcome').get_by_text('本次实际结果').wait_for()
            assert writes==['create','confirm','start']
            assert page.locator('.view.active').get_attribute('id')=='campaign'
            assert not page.locator('#executionDialog').evaluate('(node)=>node.open')
            assert 'Recorded AI lead requires verification' in page.locator('#homeOutcome').inner_text()
            assert '1 项未完成、失败或未测试' in page.locator('#homeOutcome').inner_text()
            assert '尚无已确认漏洞' in page.locator('#homeOutcome').inner_text()
            assert page.locator('#homeOutcome').get_by_text('完整运行总结').count()==1
            page.screenshot(path=str(output/'actual-results.png'),animations='disabled')
            page.set_viewport_size({'width':400,'height':900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            page.close()
        finally:
            browser.close()
    print('Passed: target-only entry, explicit authority, existing AI auto selection, single start and automatic evidence/result update; no live writes')


if __name__=='__main__':
    run()
