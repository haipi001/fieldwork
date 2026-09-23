import hashlib
import pytest
import guided_pages as pages
import guided_research as guided
import traditional_runtime as http
import final_core as core
from tests.test_final import client
from tests.test_guided_research import candidate, wait


def prepare_page_run(client,monkeypatch):
    run,_=candidate(client)
    with core.connect() as db:
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run['id'],20,0,20,0,100,0,core.utcnow()))
    monkeypatch.setattr(http,'network_guard',lambda *args:None)
    monkeypatch.setattr(core,'authorize_request',lambda *args:{'allowed':True})
    calls=[]
    def response(spec):
        calls.append(spec)
        return {'status':200,'headers':{'content-type':'text/html'},'body_preview':'<a href="/next">Next</a>','body_sha256':hashlib.sha256(b'page').hexdigest(),'body_bytes':4}
    monkeypatch.setattr(http,'request_once',response)
    return run,calls


def test_real_exchange_saved_and_visited_not_repeated(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    checkpoints=[]
    reads=pages.collect(run['id'],[],lambda rows:checkpoints.append([x.copy() for x in rows]),lambda:None)
    assert len(calls)==1 and calls[0].method=='GET' and calls[0].headers=={}
    assert checkpoints[0][0]['status']=='requesting' and reads[0]['status']=='recorded'
    with core.connect() as db:
        row=db.execute('SELECT * FROM http_exchanges WHERE id=?',(reads[0]['exchange_id'],)).fetchone()
    assert row['source']=='guided_page_read' and row['identity_id'] is None
    assert pages.collect(run['id'],[],lambda _:None,lambda:None)==[]


@pytest.mark.parametrize('url,kind,readable', [('https://example.test/delete','a',True),('https://example.test/x?q=1','a',True),('https://example.test/x','form',True),('https://example.test/x','a',False),('https://evil.test/x','a',True)])
def test_unsafe_or_unobserved_action_is_not_requested(client,monkeypatch,url,kind,readable):
    run,calls=prepare_page_run(client,monkeypatch)
    assert pages.collect(run['id'],[{'url':url,'kind':kind,'readable':readable}],lambda _:None,lambda:None)==[]
    assert calls==[]


def test_five_page_bound_and_prior_uncertain_attempt(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    links=[{'url':f'https://example.test/page/{i}','kind':'a'} for i in range(8)]
    reads=pages.collect(run['id'],links,lambda _:None,lambda:None,[links[0]['url']])
    assert len(reads)==5 and len(calls)==5
    assert all(x.url!=links[0]['url'] for x in calls)


def test_budget_denial_and_cancellation_send_nothing(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    monkeypatch.setattr(core,'authorize_request',lambda *args:{'allowed':False,'reason':'budget'})
    reads=pages.collect(run['id'],[],lambda _:None,lambda:None)
    assert reads[0]['status']=='not_completed' and not reads[0].get('attempted') and not calls
    def cancel():raise guided.Cancelled()
    with pytest.raises(guided.Cancelled):pages.collect(run['id'],[],lambda _:None,cancel)
    assert not calls


def test_continue_adds_new_page_evidence_and_no_finding(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'read_pages':True}).json())
    assert job['result']['page_reads'][0]['status']=='recorded' and job['result']['requests_sent']==1
    next_job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'read_pages':True}).json())
    assert next_job['id']!=job['id']
    assert len(calls)==2 and calls[-1].url=='https://example.test/next'
    with core.connect() as db: assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0


def test_local_http_end_to_end_scope_budget_and_increment(client):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            self.wfile.write(b'<a href="/next">Next</a><form action="/delete"></form>')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        target=f'http://127.0.0.1:{server.server_port}'
        project=client.post('/api/v1/engagements',json={'name':'Local page fixture','mode':'traditional','target':target,'scope':{'allow_private_ips':True},'policy':{'max_requests':4,'max_requests_per_second':100}}).json()
        project=client.post(f"/api/v1/engagements/{project['id']}/confirm").json()
        run_id=core.uid('run')
        with core.connect() as db:
            db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,project['id'],'traditional',project['current_scope_snapshot_id'],project['current_policy_id'],'completed','report',0,None,None,None,None,core.utcnow()))
            db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run_id,4,0,20,0,100,0,core.utcnow()))
        for _ in range(3):
            job=wait(client,client.post(f'/api/v1/runs/{run_id}/guided-research',json={'read_pages':True}).json())
            assert job['status']=='completed'
        assert seen==['/','/next']
        assert core.run_budget(run_id)['requests_used']==2
        assert job['result']['page_reads']==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
