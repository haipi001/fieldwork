"""Opt-in paid model discovery from actual public loopback browser captures.

No candidate, Claim or model answer is seeded. One model call per scenario.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
import final_core as core
import session_capture
import v5_runtime as runtime
import v5_research_worker as research
from tests.test_final import client
from tests.test_live_deepseek_research import _configured_provider
from tests.test_reviewed_replay_batch import wait as wait_job

ACCEPTANCE_OUTPUT=Path(os.environ.get('FIELDWORK_BROWSER_ACCEPTANCE_OUTPUT','build/live-browser-model-pipeline-'+uuid.uuid4().hex))

pytestmark=pytest.mark.skipif(os.getenv('FIELDWORK_LIVE_BROWSER_MODEL')!='1',reason='explicit real Chrome and paid public-fixture model acceptance')


@pytest.mark.parametrize('case',['vulnerable','fixed','legitimate_shared'])
def test_browser_model_candidate_independent_receipt_and_report(client,monkeypatch,tmp_path,case):
    ready=threading.Event();network=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cookie=self.headers.get('Cookie','')
            actor='a' if 'fixture=a' in cookie else 'b' if 'fixture=b' in cookie else None
            network.append({'path':self.path,'actor':actor});status=200;headers={};kind='application/json'
            if self.path.startswith('/login/'):
                who=self.path.rsplit('/',1)[1];headers['Set-Cookie']=f'fixture={who}; Path=/; SameSite=Lax';kind='text/html'
                body=b"<h1>Local capture fixture</h1><script>(async()=>{await fetch('/api/me');await fetch('/object');await fetch('/ready');})();</script>"
            elif self.path=='/api/me':status=200 if actor else 401;body=json.dumps({'id':actor}).encode()
            elif self.path=='/object':
                allowed=actor=='a' or (actor=='b' and case!='fixed')
                status=200 if allowed else 403;body=json.dumps({'owner_id':'a','record':'public synthetic fixture'} if allowed else {'error':'denied'}).encode()
            elif self.path=='/ready':body=b'{}';ready.set()
            else:status=404;body=b'{}'
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            for k,v in headers.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    accounts=[];captures=[];output=ACCEPTANCE_OUTPUT;output.mkdir(parents=True,exist_ok=True)
    report={'case':case,'model_calls':0,'candidate_source':'actual model hypotheses over recorded ownership fingerprints; no preseeded candidate','boundary':'Public development fixture, not holdout or native clean-start quality'}
    try:
        target=f'http://127.0.0.1:{server.server_port}'
        rule={'target':target+'/object','access':'allowlist' if case=='legitimate_shared' else 'owner_only','source':'Frozen local fixture ownership specification'}
        if case=='legitimate_shared':rule['allowed_principals']=['b']
        project=client.post('/api/v1/engagements',json={'name':'Browser model discovery fixture','target':target,
            'scope':{'allow_authentication':True,'allow_private_ips':True,'auth_allowed_hosts':['127.0.0.1'],'http_object_read_rules':[rule]},
            'policy':{'max_requests':40,'max_requests_per_second':100}}).json()
        project=client.post(f"/api/v1/engagements/{project['id']}/confirm").json();run_id=core.uid('run')
        with core.connect() as db:
            db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'paused','target',0,None,None,None,None,core.utcnow()))
            db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run_id,40,0,20,0,250000,0,core.utcnow()))
        accounts=client.post(f"/api/v1/engagements/{project['id']}/quick-identities").json()['identities']
        for account,who in zip(accounts,['a','b']):
            ready.clear();started=client.post(f"/api/v1/identities/{account['id']}/session-captures",json={'run_id':run_id,'login_url':target+'/login/'+who,'max_requests':20})
            assert started.status_code==201,started.text
            captures.append(started.json()['id']);assert ready.wait(20),'Owned browser capture did not reach ready; no duplicate started'
            completed=client.post(f"/api/v1/session-captures/{captures[-1]}/complete")
            assert completed.status_code==200,completed.text
            assert completed.json()['imported_responses']==2,completed.text
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM candidate_findings').fetchone()[0]==0
            artifacts=[dict(row) for row in db.execute("SELECT * FROM artifacts WHERE run_id=? AND kind='http.ownership_projection'",(run_id,))]
        report['capture_artifact_sha256s']={row['id']:row['sha256'] for row in artifacts}
        for row in artifacts:
            raw=Path(row['uri']).read_bytes();assert hashlib.sha256(raw).hexdigest()==row['sha256']
            assert b'fixture=a' not in raw and b'fixture=b' not in raw and b'Cookie' not in raw
            (output/(case+'-'+row['id']+'.json')).write_bytes(raw)
        source=client.get(f"/api/v1/research/engagements/{project['id']}/runs/{run_id}/campaign-source-plan")
        assert source.status_code==200,source.text
        created=client.post(f"/api/v1/engagements/{project['id']}/campaigns",json={'name':'Recorded ownership research','objective':'Review recorded ownership metadata',
            'source_run_id':run_id,'source_plan_hash':source.json()['preview_hash']})
        assert created.status_code==201,created.text
        campaign=created.json()
        with core.connect() as db:
            for row in db.execute('SELECT id,attributes_json FROM research_nodes WHERE campaign_id=?',(campaign['id'],)).fetchall():
                attrs=core.load(row['attributes_json']);assert attrs['sensitivity']=='private'
                attrs.update(sensitivity='public',acceptance_data='Public synthetic loopback metadata only')
                db.execute('UPDATE research_nodes SET attributes_json=? WHERE id=?',(core.dump(attrs),row['id']))
        with core.connect() as db:
            frozen=[dict(row) for row in db.execute('SELECT * FROM research_nodes WHERE campaign_id=? ORDER BY id',(campaign['id'],))]
            (output/(case+'-inputs.json')).write_text(core.dump(frozen))
        import runtime_secrets
        monkeypatch.setattr(runtime_secrets,'secret_root',lambda:tmp_path/'model-secrets')
        provider=_configured_provider();provider=runtime.update_provider(provider['id'],runtime.ProviderUpdate(max_output_tokens=9000))
        report['provider']={'service':'DeepSeek','model':provider['model'],'max_output_tokens':9000,'cost_rate':'conservative 10 USD per million tokens, not invoice pricing'}
        assert runtime.test_provider({'provider_id':provider['id']})['status']=='healthy'
        profile=runtime.create_profile(runtime.ProfileCreate(name='Browser capture acceptance',mode='cloud',config=runtime.ProfileConfig(
            cloud_provider_ids=[provider['id']],max_tokens_per_call=20000,max_tokens_per_hour=20000,max_concurrent_calls=1)))
        body={'campaign_id':campaign['id'],'run_id':run_id,'objective':'Review only recorded JSON ownership equality fingerprints. Propose supported hypotheses if justified; cite sources, preserve counterevidence and explain limitations. Sharing permissions remain unknown until business rules are reviewed. Recorded 200 responses alone do not confirm vulnerabilities.',
              'roles':[{'role':'researcher','count':1}],'max_concurrency':1,'max_cost_micros':250000,'max_tokens_per_task':20000,'runtime_profile_id':profile['id'],'allow_cloud_context':True}
        preview=client.post('/api/v1/orchestration/groups/team/preview',json=body);assert preview.status_code==200,preview.text
        team=client.post('/api/v1/orchestration/groups/team',json=body|{'preview_hash':preview.json()['preview_hash'],'idempotency_key':'browser-model-only-one-call'})
        assert team.status_code==201,team.text;group=team.json()['group']['id']
        launched=client.post(f'/api/v1/workers/research/groups/{group}/start');assert launched.status_code==202,launched.text
        research._jobs[group].join(timeout=55)
        report['worker_exceeded_55_seconds']=research._jobs[group].is_alive()
        # Never release this fixture's DB/secret patches while its owned worker lives.
        while research._jobs[group].is_alive():research._jobs[group].join(timeout=1)
        with core.connect() as db:
            task=dict(db.execute('SELECT * FROM agent_tasks WHERE group_id=?',(group,)).fetchone())
            claims=[dict(row) for row in db.execute("SELECT * FROM research_nodes WHERE campaign_id=? AND node_type='claim'",(campaign['id'],))]
            report.update(model_calls=db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0],task_status=task['status'],task_error=core.load(task['error_json']),
                          usage=[dict(row) for row in db.execute('SELECT input_tokens,output_tokens,cost_micros FROM runtime_usage')],claim_count=len(claims),
                          call_states=[row[0] for row in db.execute('SELECT state FROM runtime_calls')])
            (output/(case+'-claims.json')).write_text(core.dump(claims))
        assert report['model_calls']==1 and report['call_states']==['settled'] and task['status']=='succeeded',report
        selected=None
        for claim in claims:
            base=f"/api/v1/workers/research/tasks/{task['id']}/drafts/{claim['id']}"
            plan=client.get(base+'/candidate-plan')
            assert plan.status_code==200,plan.text
            if target+'/object' in plan.json()['targets']:
                selected=(base,client.get(base+'/candidate-plan',params={'target':target+'/object'}).json());break
        if case=='vulnerable':assert selected is not None,'Actual model did not propose an object-supported hypothesis'
        if selected:
            base,material=selected;assert material['proposal']['category']=='authorization',material
            saved=client.post(base+'/candidate',json={'authorized':True,'target':target+'/object','preview_hash':material['preview_hash']})
            assert saved.status_code==201,saved.text;candidate=saved.json();report['reviewed_candidate']=candidate
            workflow=f"/api/v1/runs/{run_id}/candidate-workflow/{candidate['id']}"
            replay_plan=client.get(workflow+'/execution-plan');assert replay_plan.status_code==200,replay_plan.text
            plan=replay_plan.json();report['replay_plan_blockers']=plan['blockers']
            if case=='legitimate_shared':
                assert not plan['can_execute'] and 'business_access_permitted' in plan['blockers']
            else:
                assert plan['can_execute'],plan['blockers']
                replay=client.post(workflow+'/execute',json={'authorized':True,'source_fingerprint':plan['source_fingerprint']})
                assert replay.status_code==202,replay.text;job=wait_job(client,replay.json())
                proof=job['result']['items'][0]['auto_verification']['independent_verification'];assert proof.get('receipt_id'),proof
                receipt=client.get('/api/v1/verification/receipts/'+proof['receipt_id']).json()
                assert receipt['result']['status']==('verified' if case=='vulnerable' else 'refuted')
                assert receipt['environment']['process_isolation']['network_denied'] is True
                report['independent_status']=receipt['result']['status']
                (output/(case+'-receipt.json')).write_text(core.dump(receipt))
                promotion='/api/v1/verification/receipts/'+proof['receipt_id']+'/http-finding-plan'
                finding_plan=client.get(promotion)
                if case=='vulnerable':
                    assert finding_plan.status_code==200,finding_plan.text
                    promoted=client.post(promotion.replace('http-finding-plan','promote-http'),json={'authorized':True,'source_fingerprint':finding_plan.json()['source_fingerprint']})
                    assert promoted.status_code==200,promoted.text
                    rendered=client.post('/api/v1/findings/'+promoted.json()['id']+'/reports/hackerone/preview')
                    assert rendered.status_code==200,rendered.text
                    (output/(case+'-report.md')).write_text(rendered.json()['content'])
                    report['finding_id']=promoted.json()['id']
                else:assert finding_plan.status_code==409
        with core.connect() as db:
            report['canonical_findings']=db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]
            assert report['canonical_findings']==(1 if case=='vulnerable' else 0)
        report['run_budget_requests_used']=core.run_budget(run_id)['requests_used'];report['actual_http_requests']=len(network)
    finally:
        try:
            if (output/(case+'-inputs.json')).exists():
                from scripts.archive_browser_matrix_evidence import archive
                report['registered_evidence']=archive(output,case,tmp_path)
                report['canonical_findings']=report['registered_evidence']['canonical_findings']
        finally:
            (output/(case+'.json')).write_text(json.dumps(report,indent=2))
            for capture in captures:session_capture.cancel(capture)
            for account in accounts:session_capture.delete_keychain(account['id'])
            server.shutdown();server.server_close();thread.join(timeout=2)
