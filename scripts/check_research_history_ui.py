"""Isolated browser acceptance; all mutations are fixtures, never live data."""
import json
from playwright.sync_api import sync_playwright
from check_v5_frontend_ui import BASE,CHROME,isolated_page


def check(browser):
    page=isolated_page(browser,viewport={'width':1280,'height':900})
    page.set_default_timeout(15000)
    errors=[];writes=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    projects=[{'id':'project-a','name':'OLD TARGET','mode':'traditional','status':'ready'},
              {'id':'project-b','name':'NEW TARGET','mode':'traditional','status':'ready'}]
    runs=[{'id':'run-old','engagement_id':'project-a','status':'completed'},
          {'id':'run-new','engagement_id':'project-b','status':'completed'}]
    candidates=[{'id':'candidate-old','engagement_id':'project-a','run_id':'run-old','title':'OLD CANDIDATE','status':'candidate'},
                {'id':'candidate-new','engagement_id':'project-b','run_id':'run-new','title':'NEW CANDIDATE','status':'candidate'}]
    page.route('**/api/v1/engagements?mode=traditional',lambda r:r.fulfill(json=projects))
    page.route('**/api/v1/task-center?mode=traditional',lambda r:r.fulfill(json=runs))
    page.route('**/api/v1/findings?mode=traditional',lambda r:r.fulfill(json={'candidates':candidates,'verified':[]}))
    page.route('**/api/v1/workspace/maintenance',lambda r:r.fulfill(json={'app_version':'fixture','build':1,'database_bytes':0,'schema':1,'data_directory':'fixture','backups':[]}))
    page.route('**/api/v1/maintenance/all-research',lambda r: purge(r,writes))
    job={'id':'guided-new','run_id':'run-new','status':'completed','phase':'Results saved','updated_at':'now','steps':[],
         'result':{'auto_continue':True,'automation_round':1,'items':[],'summary':'AUTOMATION COMPLETED'}}
    def start(route):
        writes.append((route.request.method,route.request.post_data_json))
        route.fulfill(json=job,status=202)
    page.route('**/api/v1/runs/run-new/guided-research',start)
    page.route('**/api/v1/runs/run-new/guided-research?batch_only=true',lambda r:r.fulfill(json=job))
    page.route('**/api/v1/runs/run-new/guided-result',lambda r:r.fulfill(json={'content':'FINAL RUN SUMMARY: 0 verified; 1 pending'}))
    print('Fixture: enter project',flush=True)
    page.goto(BASE+'/v5#campaign')
    page.locator('#homeProject').select_option('project-b')
    page.locator('#homePlan').filter(has_text='一键继续深挖').wait_for()
    print('Fixture: start continuation',flush=True)
    page.locator('#homePlan').click()
    page.locator('[data-research-run="run-new"] [type=submit]').click()
    page.locator('#candidateWorkflow').filter(has_text='FINAL RUN SUMMARY').wait_for()
    assert page.locator('#verificationContent').filter(has_text='OLD CANDIDATE').count()==0
    assert page.locator('#workflowRun').locator('option[value="run-old"]').count()==0
    assert any(body.get('auto_continue') is True for method,body in writes)
    print('Fixture: switch task/events scopes',flush=True)
    page.locator('[data-view="tasks"]').click()
    assert 'run-old' not in page.locator('#tasksContent').inner_text()
    page.locator('[data-view="events"]').click()
    page.locator('#workspace-events [name=project]').wait_for()
    assert page.locator('#workspace-events [name=project]').input_value()=='project-b'
    print('Fixture: deletion confirmation',flush=True)
    page.locator('[data-view="settings"]').click()
    page.locator('[data-w-action="purge-research"]').click()
    modal=page.locator('dialog').filter(has_text='输入 DELETE ALL')
    assert '1 条尚未开始的排队任务' in modal.inner_text()
    modal.locator('input').fill('WRONG')
    modal.locator('[type=submit]').click()
    assert not any(method=='DELETE' for method,body in writes)
    modal.locator('input').fill('DELETE ALL')
    modal.locator('[type=submit]').click()
    page.wait_for_function("!document.querySelector('dialog[open]')")
    assert any(method=='DELETE' and body['confirmation']=='DELETE_ALL_RESEARCH' for method,body in writes)
    assert not errors,errors
    page.close()


def purge(route,writes):
    if route.request.method=='GET':route.fulfill(json={'counts':{'engagements_v2':2},'pending_tasks':1,'blockers':[]})
    else:
        writes.append(('DELETE',route.request.post_data_json))
        route.fulfill(json={'status':'deleted'})


def check_blocked(browser):
    page=isolated_page(browser,viewport={'width':1280,'height':900})
    page.route('**/api/v1/workspace/maintenance',lambda r:r.fulfill(json={'app_version':'fixture','build':1,'database_bytes':0,'schema':1,'data_directory':'fixture','backups':[]}))
    page.route('**/api/v1/maintenance/all-research',lambda r:r.fulfill(json={'counts':{},'pending_tasks':1,'blockers':['runtime_calls']}))
    page.goto(BASE+'/v5#settings')
    page.locator('[data-w-action="purge-research"]').click()
    modal=page.locator('dialog[open]')
    assert '模型调用仍在进行或有未结清用量' in modal.inner_text()
    assert modal.locator('[type=submit]').is_disabled()
    assert modal.locator('input').is_disabled()
    modal.locator('[data-review-blockers]').click()
    assert page.locator('.view.active').get_attribute('id')=='runtime'
    page.close()


if __name__=='__main__':
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=CHROME,headless=True)
        try:
            check(browser)
            check_blocked(browser)
        finally:browser.close()
    print('Passed: candidate continuation, result summary, project isolation and permanent-delete confirmation; fixture writes only')
