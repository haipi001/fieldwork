from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    browser=p.chromium.launch(channel="chrome")
    page=browser.new_page()
    failed=[True]
    mutations=[]
    def api(route):
        if route.request.method not in ['GET','HEAD']:
            mutations.append(route.request.url)
        if failed[0]:
            route.abort('connectionrefused')
            return
        url=route.request.url
        body={'verified':[], 'candidates':[]} if '/findings' in url else {'counts':{},'items':[]} if '/task-center' in url else []
        route.fulfill(status=200,content_type='application/json',body=json.dumps(body))
    page.route('**/api/**', api)
    page.goto('http://127.0.0.1:8917/new')
    page.locator('#connectionIssue').wait_for(state='visible')
    page.keyboard.press('Escape')
    page.locator('#targetInput').fill('https://draft.example')
    for width in [800,1024,1440]:
        page.set_viewport_size({'width':width,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        page.screenshot(path=f'/tmp/fieldwork-boundary-{width}.png')
    # A failed check retains the warning and draft.
    page.route('**/health',lambda r:r.abort('connectionrefused'))
    page.locator('#checkConnection').click()
    page.wait_for_function("document.querySelector('#connectionIssueText').textContent.includes('仍无法连接')")
    assert page.locator('#targetInput').input_value()=='https://draft.example'
    page.unroute('**/health')
    failed[0]=False
    page.locator('#checkConnection').click()
    page.locator('#connectionIssue').wait_for(state='hidden')
    assert page.locator('#targetInput').input_value()=='https://draft.example'
    assert mutations==[], mutations
    print('UI passed: 800/1024/1440px, failed recovery, successful recovery, draft retained, no mutation retries')
    browser.close()
