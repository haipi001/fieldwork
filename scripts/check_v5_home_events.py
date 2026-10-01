"""Exercise event stream isolation without starting a real research run."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
    page = browser.new_page()
    page.add_init_script("""
      window.streams=[];
      window.EventSource=class {
        constructor(url){this.url=url;this.closed=false;window.streams.push(this)}
        close(){this.closed=true}
      };
    """)
    page.route("**/api/v1/engagements?*", lambda r: r.fulfill(json=[{"id":"home-test","name":"Event isolation","mode":"traditional","status":"ready"}]))
    page.route("**/api/v1/task-center?*", lambda r: r.fulfill(json={"items":[{"id":"home-run","engagement_id":"home-test","status":"running","completed_stages":1}]}))
    page.route("**/api/v1/runs/home-run", lambda r: r.fulfill(json={"id":"home-run","events":[]}))
    page.route("**/api/v1/runs/home-run/details", lambda r: r.fulfill(json={"artifacts":[]}))
    page.route("**/api/v1/engagements/home-test/asset-graph", lambda r: r.fulfill(json={"nodes":[],"edges":[]}))
    page.goto("http://127.0.0.1:8011/v5#campaign")
    page.wait_for_function("window.streams.length === 1", timeout=30000)
    page.evaluate("""() => {
      const s=streams[0];s.onopen();
      const send=x=>s.onmessage({data:JSON.stringify(x)});
      send({id:1,run_id:'home-run',kind:'observation',message:'First real event'});
      send({id:1,run_id:'home-run',kind:'observation',message:'First real event'});
      send({id:2,run_id:'another-run',kind:'observation',message:'Must not leak'});
    }""")
    assert page.locator("#homeEvents .data-row").count() == 1
    assert "Must not leak" not in page.locator("#homeEvents").inner_text()
    page.locator('.home-tabs [data-home-view="tasks"]').click()
    page.wait_for_function("streams[0].closed")
    page.locator('.nav-item[data-view="campaign"]').click()
    page.wait_for_function("streams.length === 2")
    page.evaluate("streams[0].onmessage({data:JSON.stringify({id:3,run_id:'home-run',message:'Stale callback'})})")
    assert "Stale callback" not in page.locator("#homeEvents").inner_text()
    page.evaluate("streams[1].onerror()")
    assert "正在重连" in page.locator("#homeEvents").inner_text()
    page.evaluate("streams[1].onmessage({data:JSON.stringify({id:4,run_id:'home-run',kind:'run.completed',message:'Finished'})})")
    assert page.evaluate("streams[1].closed")
    assert "终态事件" in page.locator("#homeEvents").inner_text()
    browser.close()
    print("Event deduplication, Run isolation, navigation cleanup, stale callback, reconnect and terminal close passed")
