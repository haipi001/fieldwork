"""Actual installed Nuclei, frozen local HTTP behavior, no injected scanner output.

A missing response header is a scanner lead, never an independently verified
vulnerability. This is tool engineering acceptance, not model quality evidence.
"""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import pytest
import final_core
from tests.test_final import client


@pytest.mark.parametrize('profile',['standard','deep'])
@pytest.mark.parametrize('fixed',[False,True])
def test_real_nuclei_header_signal_and_repaired_control(client,fixed,profile,*,real_run=False):
    template=Path.home()/'nuclei-templates/http/misconfiguration/http-missing-security-headers.yaml'
    assert template.is_file(), 'Install the reviewed Nuclei template set before acceptance'
    calls=[]
    headers=['Strict-Transport-Security','Content-Security-Policy','Permissions-Policy','X-Frame-Options',
             'X-Content-Type-Options','X-Permitted-Cross-Domain-Policies','Referrer-Policy',
             'Cross-Origin-Embedder-Policy','Cross-Origin-Opener-Policy','Cross-Origin-Resource-Policy']
    class Handler(BaseHTTPRequestHandler):
        server_version='IsolatedFixture'
        def do_GET(self):
            calls.append(self.path)
            raw=b'<html><title>Isolated response</title></html>'
            content_type='text/html'
            status=404 if self.path=='/robots.txt' else 200
            if self.path=='/.git/config':
                status=404 if fixed else 200
                raw=b'not found' if fixed else b'[core]\nrepositoryformatversion = 0\n'
                content_type='text/plain'
            elif self.path=='/openapi.json':
                raw=b'{"openapi":"3.0.0","paths":{}}'
                content_type='application/json'
            self.send_response(status)
            for header in headers:
                if header=='Content-Security-Policy' and not fixed: continue
                self.send_header(header,'fixture')
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        origin=f'http://127.0.0.1:{server.server_port}'
        created=client.post('/api/v1/engagements',json={'name':'Real scanner control','target':origin,
            'mode':'traditional','scope':{'allow_private_ips':True},'policy':{'max_requests_per_second':10}}).json()
        engagement=client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
        if real_run:
            # Direct, non-synthetic single-tool harness; not a completed full pipeline.
            run={'id':final_core.uid('run')}
            with final_core.connect() as db:
                db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (run['id'],engagement['id'],'traditional',engagement['current_scope_snapshot_id'],engagement['current_policy_id'],
                            'queued','scanner',0,None,None,None,None,final_core.utcnow()))
                db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run['id'],150,0,10,0,100,0,final_core.utcnow()))
        else:
            run=client.post(f"/api/v1/engagements/{engagement['id']}/start").json()
        with final_core.connect() as db:
            db.execute('UPDATE run_budgets_v2 SET request_limit=150,tool_call_limit=10 WHERE run_id=?',(run['id'],))
        response=client.post(f"/api/v1/traditional/runs/{run['id']}/tools/run",json={'capability':'nuclei','scan_profile':profile,'timeout_seconds':45})
        assert response.status_code==200,response.text
        result=response.json()
        assert result['tool_result']['status']=='completed',result['tool_result']
        assert calls and result['tool_result']['network_transport']['responses']==len(calls)
        with final_core.connect() as db:
            observations=db.execute('SELECT summary,observation_type FROM observations WHERE run_id=?',(run['id'],)).fetchall()
        missing=[row['summary'] for row in observations if 'Missing Security Headers' in row['summary']]
        assert bool(missing) is (not fixed), observations
        if profile=='deep':
            assert any(row['observation_type']=='surface.api_description' for row in observations)
            git=[row for row in observations if 'Git Configuration' in row['summary']]
            assert bool(git) is (not fixed)
        correlated=client.post(f"/api/v1/runs/{run['id']}/correlate")
        assert correlated.status_code==200,correlated.text
        assert (correlated.json()['count'] > 0) is (not fixed)
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM canonical_findings WHERE engagement_id=?',(engagement['id'],)).fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        template_sha=hashlib.sha256(template.read_bytes()).hexdigest()
        assert template_sha in result['tool_result']['network_transport']['template_sha256'].values()
        with final_core.connect() as db:
            artifact=db.execute('SELECT uri,sha256 FROM artifacts WHERE id=?',(result['artifact_id'],)).fetchone()
        assert hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest()==artifact['sha256']
        proof={'case':'repaired' if fixed else 'missing_csp','actual_tool':'nuclei','profile':profile,
               'requests':len(calls),'scanner_signal':bool(missing),'automatic_candidates':correlated.json()['count'],
               'verified_findings':0,'model_calls':0,'template_sha256':template_sha,'artifact_sha256':artifact['sha256'],
               'stdout_sha256':result['tool_result']['stdout_sha256']}
        output=Path(__file__).resolve().parents[1]/'build/actual-scanner-discovery';output.mkdir(parents=True,exist_ok=True)
        (output/(proof['case']+'-'+profile+'.json')).write_text(json.dumps(proof,indent=2))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


@pytest.mark.parametrize('template,severity,tags,kind',[
    ('tech-detect','info',['tech','discovery'],'surface.technology'),
    ('other-tech','info','tech,discovery','surface.technology'),
    ('security-check','high',['tech','vuln'],'scanner.template_match'),
    ('missing-header','info',['misconfig','headers'],'scanner.template_match'),
    ('openapi','info',['exposure','api','discovery'],'surface.api_description'),
    ('swagger-api','info',['exposure','api','discovery'],'surface.api_description'),
])
def test_nuclei_discovery_classification_preserves_security_signals(template,severity,tags,kind):
    import traditional_tools
    raw=json.dumps({'template-id':template,'matched-at':'http://fixture.test','info':{'name':'fixture','severity':severity,'tags':tags}})
    assert traditional_tools.parse_output('nuclei',raw)[0]['observation_type']==kind
