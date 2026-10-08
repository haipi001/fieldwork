import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import final_core
import native_agent
from tests.test_final import client


def prepare(client, target, profile):
    engagement = client.post('/api/v1/engagements', json={'name':'Native discovery fixture',
        'mode':'traditional','target':target,'scope':{'allow_private_ips':True}}).json()
    engagement = client.post(f'/api/v1/engagements/{engagement["id"]}/confirm').json()
    with final_core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            'native-discovery-run',engagement['id'],'traditional',engagement['current_scope_snapshot_id'],
            engagement['current_policy_id'],'running','target',0,None,None,None,
            None,final_core.utcnow()))
        db.execute('INSERT INTO run_configs_v2 VALUES(?,?,?)', (
            'native-discovery-run',json.dumps({'native_runtime_profile_id':profile}),final_core.utcnow()))
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)', (
            'native-discovery-run',2,0,2,0,1_000_000,0,final_core.utcnow()))
    return engagement


def test_browser_discovery_uses_pinned_get_and_v5_model_ledger(client, monkeypatch, tmp_path):
    pytest.importorskip('playwright')
    monkeypatch.setattr(native_agent, 'WORKSPACE_ROOT', tmp_path/'native')
    hits, model_hits = [], []
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(('GET',self.path))
            raw = b'<html><title>Read only fixture</title><body>Recorded public fixture page<a href="/next">next</a><img src="/pixel"><script>fetch("/danger",{method:"POST"});</script></body></html>'
            self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            hits.append(('POST',self.path)); self.send_response(200); self.end_headers()
        def log_message(self,*_):
            pass
    class Model(BaseHTTPRequestHandler):
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            context=json.loads(data['messages'][1]['content'])['native_discovery_context']
            model_hits.append(data)
            first=not context['current_page']['url'].endswith('/next')
            output={'action':'navigate' if first else 'finish','url':target_url+'/next',
                    'reason':'Inspect one recorded in-scope link','hypotheses':[]}
            raw=json.dumps({'choices':[{'message':{'content':json.dumps(output)}}],
                'usage':{'prompt_tokens':30,'completion_tokens':20}}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def log_message(self,*_):
            pass
    target=ThreadingHTTPServer(('127.0.0.1',0),Target)
    model=ThreadingHTTPServer(('127.0.0.1',0),Model)
    target_url=f'http://127.0.0.1:{target.server_port}'
    threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (target,model)]
    for thread in threads:thread.start()
    try:
        with final_core.connect() as db:
            now=final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'native-fixture-model','llama_cpp','Native model protocol fixture',f'http://127.0.0.1:{model.server_port}',
                'fixture',None,1,'{"location":"local"}','healthy',now,now,now))
        response=client.put('/api/v1/runtime/config',json={'name':'Native local Profile','mode':'local',
            'config':{'local_provider_ids':['native-fixture-model']}})
        assert response.status_code==201,response.text
        prepare(client,target_url,response.json()['id'])
        result=native_agent.run_native_agent('native-discovery-run',max_turns=2)
        assert result['status']=='completed' and result['pages']==2 and result['candidate_ids']==[]
        assert hits==[('GET','/'),('GET','/next')]
        assert len(model_hits)==2 and all(value['max_tokens']<=4000 for value in model_hits)
        with final_core.connect() as db:
            assert db.execute('SELECT requests_used FROM run_budgets_v2 WHERE run_id=?',
                              ('native-discovery-run',)).fetchone()[0]==2
            assert db.execute("SELECT COUNT(*) FROM runtime_calls WHERE state='settled'").fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM runtime_call_inputs').fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM runtime_call_timings').fetchone()[0]==2
            assert db.execute("SELECT COUNT(*) FROM runtime_artifact_links_v6 l "
                              "JOIN artifacts a ON a.id=l.artifact_id "
                              "WHERE a.kind='native_agent.browser_observation'").fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM candidate_findings').fetchone()[0]==0
        with pytest.raises(RuntimeError,match='checkpoint_review'):
            native_agent.run_native_agent('native-discovery-run',max_turns=2)
        assert len(hits)==2 and len(model_hits)==2
    finally:
        for server in (target,model):server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=2)


def test_native_requires_selected_local_profile_before_tool_requests(client, monkeypatch, tmp_path):
    monkeypatch.setattr(native_agent,'readiness',lambda:{'available':True,'configured':True})
    prepare(client,'https://native.example.test',None)
    monkeypatch.setattr(native_agent,'_observe_page',lambda *_:pytest.fail('must not observe without profile'))
    with pytest.raises(Exception,match='selected V5 Profile'):
        native_agent.run_native_agent('native-discovery-run')
