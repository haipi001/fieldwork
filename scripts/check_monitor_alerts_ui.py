"""Isolated real API local alert inbox; synthetic policy sources, no collection or models."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
import json,os,secrets,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app,final_core,monitor_startup

with tempfile.TemporaryDirectory(prefix='fieldwork-login-ui-') as directory,ExitStack() as stack:
    root=Path(directory).resolve()
    for module,key,value in [(app,'DB',root/'ui.db'),(app,'DATA',root),(final_core,'DB',root/'ui.db'),(final_core,'LOCAL_DATA_ROOT',root)]:stack.enter_context(patch.object(module,key,value))
    stack.enter_context(patch.dict(os.environ,{'FIELDWORK_SESSION_TOKEN':secrets.token_urlsafe(48),'FIELDWORK_DISABLE_AGENT_MONITOR':'1','FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER':'1'}))
    parent=root/'fake-home/Library';parent.mkdir(parents=True)
    label='com.fieldwork.monitor.ui-'+__import__('hashlib').sha256(str(root).encode()).hexdigest()[:12]
    stack.enter_context(patch.object(monitor_startup,'_location',lambda:(label,parent/'LaunchAgents'/(label+'.plist'))))
    state={'registered':False,'commands':[]}
    def command(*args):
        state['commands'].append(args[0])
        if args[0]=='print':return SimpleNamespace(returncode=0 if state['registered'] else 113,stderr=b'' if state['registered'] else b'Could not find service')
        if args[0]=='bootstrap':state['registered']=True
        if args[0]=='bootout':state['registered']=False
        return SimpleNamespace(returncode=0,stderr=b'')
    stack.enter_context(patch.object(monitor_startup,'_command',command))
    app.prepare_database_upgrade(root/'ui.db',root/'backups',ROOT);app.init_db();final_core.init_final_db();app.apply_v5_schema(root/'ui.db');app.apply_v6_schema(root/'ui.db');app.finalize_database_version(root/'ui.db')
    client=stack.enter_context(TestClient(app.app,base_url='http://127.0.0.1:8000',headers={'X-Fieldwork-Session':os.environ['FIELDWORK_SESSION_TOKEN']}))
    import agent_monitor_windows as windows
    import agent_audit as audit
    from tests.test_monitor_alerts import setup_monitor
    import monitor_alerts
    aid=setup_monitor(client,audit.demo_fixture())
    errors=[];writes=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script('''window.__nativeActions=[];window.__nativeEnabled=false;
          window.webkit={messageHandlers:{fieldworkNotifications:{postMessage(body){
            window.__nativeActions.push(body);
            if(body.action==='enable')window.__nativeEnabled=true;
            if(body.action==='disable')window.__nativeEnabled=false;
            setTimeout(()=>window.dispatchEvent(new CustomEvent('fieldwork:native-alert-state',{detail:{enabled:window.__nativeEnabled,authorization:'authorized',state:window.__nativeEnabled?'enabled':'disabled',app_running_only:true}})),0);
          }}}};''')

        def route(route):
            req=route.request;url=urlparse(req.url);assert url.hostname=='127.0.0.1'
            if url.path.startswith('/static/'):route.fulfill(path=str(ROOT/url.path.lstrip('/')));return
            if req.method!='GET':writes.append(url.path)
            result=client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=result.status_code,body=result.content,content_type=result.headers.get('content-type','application/json'))
        page.route('**/*',route);page.goto('http://127.0.0.1:8000/v5#sentinel')
        form=page.locator('[data-alert-config]');form.wait_for()
        aid=setup_monitor(client,audit.demo_fixture())  # Created after initial audit-list load.
        form.locator('[name=enabled]').check();form.locator('button').click()
        assert '/api/v1/workspace/monitor-alerts/configuration' not in writes
        form.locator('[name=confirmed]').check();form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-alerts]')?.textContent.includes('本机派发已启用')")
        assert monitor_alerts.configuration()['enabled']
        native=page.locator('[data-w-native-alerts]')
        native.locator('[data-native-alert-enable] button').click()
        assert page.evaluate("window.__nativeActions.filter(a=>a.action==='enable').length")==0
        native.locator('[name=confirmed]').check();native.locator('[data-native-alert-enable] button').click()
        page.wait_for_function("document.querySelector('[data-native-alert-status]')?.textContent.includes('App 通知开关已启用')")
        assert page.evaluate("window.__nativeActions.filter(a=>a.action==='enable').length")==1
        native.locator('[data-native-alert-disable]').click()
        page.wait_for_function("document.querySelector('[data-native-alert-status]')?.textContent.includes('App 通知开关已关闭')")
        assert page.evaluate("window.__nativeActions.every(a=>Object.keys(a).every(k=>['action','language'].includes(k)))")

        windows.tick(aid)
        page.locator('#workspace-sentinel [data-w-action=refresh]').click()
        page.locator('[data-alert-source]').first.wait_for()
        page.locator('[data-alert-source]').first.click()
        page.locator('[data-alert-open-audit]').wait_for()
        assert 'not_verified' in page.locator('[data-alert-source-detail]').text_content()
        assert page.locator('[data-alert-source-detail] tbody tr').count()>0
        replay_form=page.locator('[data-alert-replay]')
        replay_form.locator('button').click()
        assert not any(path.endswith('/reconstruction') for path in writes)
        replay_form.locator('[name=confirmed]').check();replay_form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-alert-replay-result]')?.textContent.includes('策略判定重放一致')")
        assert 'human_review' in page.locator('[data-alert-replay-result]').text_content()
        page.locator('#workspace-sentinel [data-w-action=refresh]').click()
        page.locator('[data-alert-source]').first.wait_for();page.locator('[data-alert-source]').first.click()
        page.wait_for_function("document.querySelector('[data-alert-replay-result]')?.textContent.includes('策略判定重放一致')")
        assert len([path for path in writes if path.endswith('/reconstruction')])==1
        page.locator('[data-alert-open-audit]').click()
        page.wait_for_function("document.querySelector('.monitor-history')?.open===true")
        assert page.locator('#sentinelAudit').input_value()==aid
        page.locator('#workspace-sentinel').scroll_into_view_if_needed()
        page.locator('[data-alert-ack]').first.click()
        page.wait_for_function("document.querySelector('[data-w-monitor-alerts]')?.textContent.includes('已处理')")
        assert monitor_alerts.inbox()['items'][0]['acknowledged_at']
        form=page.locator('[data-alert-config]');form.locator('[name=enabled]').uncheck();form.locator('[name=confirmed]').check();form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-alerts]')?.textContent.includes('本机派发已关闭')")
        assert not monitor_alerts.configuration()['enabled']
        page.locator('#languageToggle').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-alerts]')?.textContent.includes('Local delivery disabled')")
        for width in (1440,1024,680,375):
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
        page.evaluate('delete window.webkit')
        page.locator('#workspace-sentinel [data-w-action=refresh]').click()
        page.locator('[data-w-native-alerts]').filter(has_text='Configure system notifications in the installed Fieldwork app.').wait_for()
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert not errors,errors
        browser.close()
    print(json.dumps({'page_errors':errors,'startup_posts':writes.count('/api/v1/workspace/monitor-startup'),'bootstrap':state['commands'].count('bootstrap'),'bootout':state['commands'].count('bootout'),'alert_config_posts':writes.count('/api/v1/workspace/monitor-alerts/configuration'),'scope':'real APIs and synthetic policy source in isolated DB; no real collection, models or external notification'}))
