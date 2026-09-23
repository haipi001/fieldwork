"""Opt-in real isolated Chrome / Keychain / HTTP oracle integration on loopback only."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import final_core as core
import session_capture
from tests.test_final import client
from tests.test_guided_research import wait


@pytest.mark.skipif(os.getenv('FIELDWORK_TEST_BROWSER')!='1',reason='explicit visible isolated Chrome fixture')
@pytest.mark.parametrize('vulnerable',[True,False])
def test_login_capture_to_automatic_verification(client,vulnerable):
    ready=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cookie=self.headers.get('Cookie','')
            account='a' if 'fixture=a' in cookie else 'b' if 'fixture=b' in cookie else None
            status=200;headers={};content_type='application/json'
            if self.path.startswith('/login/'):
                who=self.path.rsplit('/',1)[1]
                headers['Set-Cookie']=f'fixture={who}; Path=/; SameSite=Lax'
                content_type='text/html'
                body=("<h1>Local login fixture</h1><script>(async()=>{await fetch('/api/me');"+("await fetch('/object');" if who=='a' else '')+"await fetch('/ready');})();</script>").encode()
            elif self.path=='/api/me':
                status=200 if account else 401;body=json.dumps({'id':account}).encode()
            elif self.path=='/object':
                allowed=account=='a' or (account=='b' and vulnerable)
                status=200 if allowed else 403;body=json.dumps({'owner_id':'a'} if allowed else {'error':'denied'}).encode()
            elif self.path=='/ready':
                body=b'{}';ready.set()
            else:
                status=404;body=b'{}'
            self.send_response(status)
            self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            for key,value in headers.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    accounts=[];captures=[]
    try:
        target=f'http://127.0.0.1:{server.server_port}'
        project=client.post('/api/v1/engagements',json={'name':'Browser login fixture','target':target,'mode':'traditional','scope':{'allow_authentication':True,'allow_private_ips':True},'policy':{'max_requests_per_second':50,'max_requests':30}}).json()
        project=client.post(f"/api/v1/engagements/{project['id']}/confirm").json()
        run_id=core.uid('run')
        with core.connect() as db:
            db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'completed','report',0,None,None,None,None,core.utcnow()))
            db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run_id,30,0,20,0,100,0,core.utcnow()))
        observation_id=core.uid('obs')
        with core.connect() as db:
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(observation_id,run_id,project['id'],'traditional','authorization',target+'/object','Local fixture object boundary requires independent verification',.5,'local-fixture',None,core.utcnow()))
        candidate=client.post(f'/api/v1/runs/{run_id}/candidates',json={'title':'Object ownership fixture','category':'authorization','target':target+'/object','hypothesis':'Check recorded object ownership boundary','observation_ids':[observation_id]}).json()
        accounts=client.post(f"/api/v1/engagements/{project['id']}/quick-identities").json()['identities']
        initial=wait(client,client.post(f'/api/v1/runs/{run_id}/guided-research',json={'execute_ready':True}).json())
        assert initial['status']=='awaiting_input'
        for account,who in zip(accounts,['a','b']):
            ready.clear()
            started=client.post(f"/api/v1/identities/{account['id']}/session-captures",json={'run_id':run_id,'login_url':target+'/login/'+who,'max_requests':30})
            assert started.status_code==201,started.text
            capture_id=started.json()['id'];captures.append(capture_id)
            assert ready.wait(20),'isolated Chrome fixture did not finish its local page'
            completed=client.post(f'/api/v1/session-captures/{capture_id}/complete')
            assert completed.status_code==200,completed.text
            assert completed.json()['imported_responses']==(2 if who=='a' else 1)
            latest=wait(client,client.get(f'/api/v1/runs/{run_id}/guided-research').json())
        item=latest['result']['items'][0]
        assert item['auto_verification']['status']==('reproduced' if vulnerable else 'not_established')
        assert latest['result']['requests_sent']==10
        assert core.run_budget(run_id)['requests_used']==10
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
    finally:
        for capture_id in captures:session_capture.cancel(capture_id)
        for account in accounts:session_capture.delete_keychain(account['id'])
        server.shutdown();server.server_close();thread.join(timeout=2)
