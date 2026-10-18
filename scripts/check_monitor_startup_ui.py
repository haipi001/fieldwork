"""Isolated real API login controls with a fake launchctl; no user login setting changes."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
import json,os,secrets,sys,tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
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
    errors=[];writes=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(route):
            req=route.request;url=urlparse(req.url);assert url.hostname=='127.0.0.1'
            if url.path.startswith('/static/'):route.fulfill(path=str(ROOT/url.path.lstrip('/')));return
            if req.method!='GET':writes.append(url.path)
            result=client.request(req.method,url.path+('?' + url.query if url.query else ''),content=req.post_data,headers={'Content-Type':'application/json'})
            route.fulfill(status=result.status_code,body=result.content,content_type=result.headers.get('content-type','application/json'))
        page.route('**/*',route);page.goto('http://127.0.0.1:8000/v5#sentinel')
        form=page.locator('[data-w-monitor-login]');form.wait_for()
        assert not form.locator('[name=enabled]').is_checked()
        form.locator('[name=enabled]').check();form.locator('button').click()
        assert '/api/v1/workspace/monitor-startup' not in writes
        form.locator('[name=confirmed]').check();form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-startup]')?.textContent.includes('登录任务已配置并注册')")
        assert state['registered'] and state['commands'].count('bootstrap')==1
        form=page.locator('[data-w-monitor-login]');form.locator('[name=enabled]').uncheck();form.locator('[name=confirmed]').check();form.locator('button').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-startup]')?.textContent.includes('尚未启用登录启动')")
        assert not state['registered'] and state['commands'].count('bootout')==1
        page.locator('#languageToggle').click()
        page.wait_for_function("document.querySelector('[data-w-monitor-startup]')?.textContent.includes('Login startup is not enabled')")
        page.set_viewport_size({'width':680,'height':1000})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM agent_monitors WHERE status='active'").fetchone()[0]==0
        assert not errors,errors
        browser.close()
    print(json.dumps({'page_errors':errors,'startup_posts':writes.count('/api/v1/workspace/monitor-startup'),'bootstrap':state['commands'].count('bootstrap'),'bootout':state['commands'].count('bootout'),'scope':'real APIs, isolated files and fake launchctl; no real login configuration or collection'}))
