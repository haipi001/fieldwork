"""Automatic team defaults, explicit combined start and passive result refresh."""
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import isolated_page, BASE, CHROME


def run():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=CHROME,headless=True)
        try:
            page=isolated_page(browser,viewport={'width':1280,'height':950})
            state={'created':False,'started':False,'reads':0}
            writes=[];errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            project={'id':'team-project','name':'Automatic team example','status':'ready','confirmed_at':'2026-10-14'}
            group={'id':'auto-group','campaign_id':'team-campaign','run_id':'team-run','status':'active','strategy':'bounded_research',
                   'objective':'Review recorded evidence','runtime_profile_id':'existing-model','usage':{'task_count':1},
                   'budget':{'max_concurrency':1,'max_cost_micros':50000}}
            page.route('**/api/v1/engagements?mode=traditional',lambda r:r.fulfill(json=[project]))
            page.route('**/api/v1/engagements/team-project/campaigns',lambda r:r.fulfill(json=[{'id':'team-campaign','name':'Existing campaign','status':'active','objective':'Review recorded evidence'}]))
            page.route('**/api/v1/runs?mode=traditional',lambda r:r.fulfill(json=[{'id':'team-run','engagement_id':'team-project','status':'completed'}]))
            page.route('**/api/v1/runtime/config',lambda r:r.fulfill(json={'providers':[],'profiles':[{'id':'existing-model','name':'Connected local AI','mode':'local'}]}))
            page.route('**/api/v1/engagements/team-project/assisted-plan*',lambda r:r.fulfill(json={'ai':{'enabled':True,'profile_id':'existing-model','name':'Connected local AI'},
                'team_defaults':{'max_concurrency':1,'max_cost_micros':50000,'max_tokens_per_task':1024,'max_runtime_ms_per_task':1200}}))
            page.route('**/api/v1/orchestration/status',lambda r:r.fulfill(json={'runners':[]}))
            page.route('**/api/v1/orchestration/groups?*',lambda r:r.fulfill(json={'items':[group] if state['created'] else []}))
            def tasks(route):
                if state['started']:state['reads']+=1
                finished=state['reads']>=2
                route.fulfill(json={'items':[{'id':'auto-task','group_id':'auto-group','role':'researcher','objective':'Review recorded evidence',
                    'status':'succeeded' if finished else 'running' if state['started'] else 'queued','attempt':1,'max_attempts':1,
                    'result':{'status':'research_draft','summary':'Automatically recorded result with evidence references','claim_ids':['unverified-claim'],'open_question_ids':[]} if finished else None}] if state['created'] else []})
            page.route('**/api/v1/orchestration/tasks?*',tasks)
            def preview(route):
                body=route.request.post_data_json
                assert body['runtime_profile_id']=='existing-model' and body['max_tokens_per_task']==1024
                assert body['max_cost_micros']==50000 and not body['allow_cloud_context']
                route.fulfill(json={**body,'task_count':1,'preview_hash':'b'*64,'cloud_context_approved':False,'research_input_hashes':{}})
            def create(route):
                writes.append('create');state['created']=True
                assert route.request.post_data_json['preview_hash']=='b'*64
                route.fulfill(status=201,json={'group':group,'tasks':[]})
            def start(route):
                assert state['created'];writes.append('start');state['started']=True
                route.fulfill(status=202,json={'status':'started'})
            page.route('**/api/v1/orchestration/groups/team/preview',preview)
            page.route('**/api/v1/orchestration/groups/team',create)
            page.route('**/api/v1/workers/research/groups/auto-group/start',start)
            page.goto(BASE+'/v5#agents')
            page.wait_for_function("document.querySelector('#teamCreate')&&!document.querySelector('#teamCreate').disabled")
            page.locator('#teamCreate').click()
            page.wait_for_function("!document.querySelector('#teamCommit').disabled")
            assert not page.locator('#teamAdvanced').evaluate('(node)=>node.open')
            assert page.locator('#teamProfile').input_value()=='existing-model'
            assert not writes
            page.locator('#teamAccepted').check()
            page.locator('#teamCommit').click()
            page.get_by_text('Automatically recorded result with evidence references').wait_for(timeout=15000)
            assert writes==['create','start']
            assert '未验证草稿' in page.locator('#agentsContent').inner_text()
            assert not errors,errors
            page.close()
        finally:
            browser.close()
    print('Passed: existing AI and bounded defaults auto selected, explicit one-step create/start and automatic result refresh; no live inference')


if __name__=='__main__':
    run()
