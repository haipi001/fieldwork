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
@pytest.mark.parametrize('case',['vulnerable','fixed','legitimate_shared'])
def test_login_capture_to_automatic_verification(client,case):
    vulnerable=case!='fixed'
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
                body=("<h1>Local login fixture</h1><script>(async()=>{await fetch('/api/me');"+"await fetch('/object');"+"await fetch('/ready');})();</script>").encode()
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
        rule={'target':target+'/object','access':'allowlist' if case=='legitimate_shared' else 'owner_only',
              'source':'Frozen loopback acceptance ownership specification'}
        if case=='legitimate_shared':rule['allowed_principals']=['b']
        project=client.post('/api/v1/engagements',json={'name':'Browser login fixture','target':target,'mode':'traditional','scope':{'allow_authentication':True,'auth_allowed_hosts':['127.0.0.1'],'allow_private_ips':True,'http_object_read_rules':[rule]},'policy':{'max_requests_per_second':50,'max_requests':30}}).json()
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
            assert completed.json()['imported_responses']==2
            latest=wait(client,client.get(f'/api/v1/runs/{run_id}/guided-research').json())
        item=latest['result']['items'][0]
        assert item['auto_verification']['status']==('reproduced' if vulnerable else 'not_established')
        boundary=item['auto_verification']['business_boundary']
        assert boundary['status']==('permitted' if case=='legitimate_shared' else 'denied')
        assert boundary['reason']==('access_permitted_by_rule' if case=='legitimate_shared' else 'access_denied_by_rule')
        assert latest['result']['requests_sent']==10
        assert core.run_budget(run_id)['requests_used']==10
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
            artifact=db.execute('SELECT uri,sha256 FROM artifacts WHERE id=?',(item['auto_verification']['artifact_id'],)).fetchone()
        # Continue from actual browser/Keychain inputs through the reviewed isolated path.
        # Never mint a claim/receipt/result directly in this acceptance test.
        path=f"/api/v1/runs/{run_id}/candidate-workflow/{candidate['id']}"
        checked=client.get(path+'/execution-plan')
        assert checked.status_code==200,checked.text
        plan=checked.json()
        independent=None;report_digest=None;formal_count=0;receipt_record=None;report_content=None
        if case=='legitimate_shared':
            assert plan['can_execute'] is False and 'business_access_permitted' in plan['blockers']
            refused=client.post(path+'/execute',json={'authorized':True,'source_fingerprint':plan['source_fingerprint']})
            assert refused.status_code==409
        else:
            assert plan['can_execute'],plan['blockers']
            launched=client.post(path+'/execute',json={'authorized':True,'source_fingerprint':plan['source_fingerprint']})
            assert launched.status_code==202,launched.text
            reviewed=wait(client,launched.json())
            outcome=reviewed['result']['items'][0]['auto_verification']
            independent=outcome.get('independent_verification',{})
            assert independent.get('receipt_id'),reviewed
            receipt=client.get('/api/v1/verification/receipts/'+independent['receipt_id']).json()
            assert receipt['result']['status']==('verified' if case=='vulnerable' else 'refuted'),receipt
            receipt_record=receipt
            isolation=receipt['environment']['process_isolation']
            assert isolation['network_denied'] is True and isolation['child_pid']!=isolation['parent_pid']
            base='/api/v1/verification/receipts/'+independent['receipt_id']
            finding_plan=client.get(base+'/http-finding-plan')
            if case=='fixed':
                assert finding_plan.status_code==409
            else:
                assert finding_plan.status_code==200,finding_plan.text
                material=finding_plan.json()
                assert material['sends_requests'] is False
                promoted=client.post(base+'/promote-http',json={'authorized':True,'source_fingerprint':material['source_fingerprint']})
                assert promoted.status_code==200,promoted.text
                preview=client.post('/api/v1/findings/'+promoted.json()['id']+'/reports/hackerone/preview')
                assert preview.status_code==200,preview.text
                assert 'Broader data access and severity are unproven' in preview.json()['content']
                import hashlib
                report_content=preview.json()['content']
                report_digest=hashlib.sha256(report_content.encode()).hexdigest()
                formal_count=1
        expected_requests=10 if case=='legitimate_shared' else 20
        assert core.run_budget(run_id)['requests_used']==expected_requests
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==formal_count
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        import hashlib
        from pathlib import Path
        assert hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest()==artifact['sha256']
        output=Path(__file__).resolve().parents[1]/'build/browser-auth-report-ownership-20261019';output.mkdir(parents=True,exist_ok=True)
        if receipt_record is not None:(output/(case+'-receipt.json')).write_text(json.dumps(receipt_record,indent=2))
        if report_content is not None:(output/(case+'-report.md')).write_text(report_content)
        (output/(case+'.json')).write_text(json.dumps({'case':case,'actual_browser':'isolated Chrome','captures':len(captures),
            'replay_status':item['auto_verification']['status'],'business_boundary':boundary,'initial_requests_sent':10,
            'budget_requests_used':core.run_budget(run_id)['requests_used'],'artifact_sha256':artifact['sha256'],
            'reviewed_plan_blockers':plan['blockers'],'independent_verification':independent,'report_content_sha256':report_digest,
            'canonical_findings':formal_count,'model_calls':0,'candidate_source':'explicit local fixture, not model discovery'},indent=2))
    finally:
        for capture_id in captures:session_capture.cancel(capture_id)
        for account in accounts:session_capture.delete_keychain(account['id'])
        server.shutdown();server.server_close();thread.join(timeout=2)
