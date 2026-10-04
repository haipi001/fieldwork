import hashlib
import json

import pytest
import guided_http
import final_core
import traditional_runtime as http
from tests.test_guided_research import client, candidate


def records():
    def row(id,url,identity,body):
        return dict(id=id,url=url,identity_id=identity,method='GET',response_status=200,
                    response_body_preview=json.dumps(body),response_sha256=id)
    return [row('object','https://example.test/object','owner',{'owner_id':'A','private':'value'}),
            row('me-a','https://example.test/api/me','owner',{'id':'A'}),
            row('me-b','https://example.test/api/me','other',{'id':'B'})]


def identities():
    return [dict(id=id,session_status='ready',expires_at=None) for id in ('owner','other')]


def test_plan_uses_observed_identity_object_binding():
    c=dict(mode='traditional',category='authorization',status='candidate',target='https://example.test/object')
    plan=guided_http.plan(c,records(),identities())
    assert plan['owner_field']=='owner_id' and plan['principal_field']=='id'
    assert plan['sources']==['object','me-a','me-b']
    assert plan['requests']==10 and 'headers' not in plan


@pytest.mark.parametrize('change',['missing_me','same_user','expired','ambiguous','cross_origin'])
def test_missing_or_ambiguous_material_never_guessed(change):
    rows=records();ids=identities()
    if change=='missing_me': rows=rows[:1]
    if change=='same_user': rows[2]['response_body_preview']='{"id":"A"}'
    if change=='expired': ids[1]['expires_at']='2000-01-01T00:00:00+00:00'
    if change=='ambiguous':
        rows[1]['response_body_preview']='{"id":"A","sub":"A"}'
        rows[2]['response_body_preview']='{"id":"B","sub":"B"}'
    if change=='cross_origin': rows[2]['url']='https://other.test/api/me'
    assert guided_http.plan(dict(mode='traditional',category='authorization',status='candidate',target='https://example.test/object'),rows,ids) is None


@pytest.mark.parametrize('vulnerable',[True,False])
def test_real_oracle_path_runs_two_rounds_without_formal_promotion(client,monkeypatch,vulnerable):
    run,c=candidate(client)
    detail=client.get(f"/api/v1/candidates/{c['id']}").json()['candidate']
    binding=guided_http.plan(detail,records(),identities())
    engagement=final_core.get_engagement(detail['engagement_id'])
    engagement['confirmed_at']='2026-09-12T00:00:00Z';engagement['scope']['allow_authentication']=True
    engagement['policy']['max_requests_per_second']=1000
    monkeypatch.setattr(final_core,'get_engagement',lambda id:engagement)
    monkeypatch.setattr(final_core,'get_identity',lambda id:dict(id=id,engagement_id=detail['engagement_id'],session_status='ready',expires_at=None))
    monkeypatch.setattr(http,'resolve_identity_headers',lambda id:{'Authorization':id})
    guards=[];requests=[]
    monkeypatch.setattr(http,'network_guard',lambda engagement,spec:guards.append(spec.url))
    def request(spec):
        requests.append(spec)
        identity=spec.headers.get('Authorization')
        if spec.url.endswith('/me'):
            status=200;body=json.dumps({'id':'A' if identity=='owner' else 'B'})
        elif not identity or (not vulnerable and identity=='other'):
            status=403;body='denied'
        else:
            status=200;body=json.dumps({'owner_id':'A','private':'value'})
        return dict(status=status,body_sha256=hashlib.sha256(body.encode()).hexdigest(),body_bytes=len(body),headers={},body_preview=body,_transient_body=body)
    monkeypatch.setattr(http,'request_once',request)
    with final_core.connect() as db:
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run['id'],20,0,10,0,0,0,final_core.utcnow()))
    result=guided_http.execute(detail,binding,lambda *args:None)
    assert len(requests)==10 and len(guards)==15
    assert result['status']==('reproduced' if vulnerable else 'not_established')
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT requests_used FROM run_budgets_v2 WHERE run_id=?',(run['id'],)).fetchone()[0]==10
    assert 'Authorization' not in json.dumps(result)


def test_guided_job_consumes_generated_binding_when_enabled(client,monkeypatch):
    import guided_research as guided
    from tests.test_guided_research import wait
    run,c=candidate(client)
    data=guided.snapshot(run['id']);data['exchanges']=records();data['identities']=identities()
    monkeypatch.setattr(guided,'snapshot',lambda *args:data.copy())
    called=[]
    def execute(c,binding,before,after,*,checkpoint_callback=None):
        called.append(binding)
        for index in range(10):
            before(index//5,'fixture')
            checkpoint_callback({'completed_responses': index + 1, 'artifact_id': 'fixture-checkpoint',
                                 'required_requests': 10, 'state': 'in_progress', 'promotion_eligible': False})
            after()
        return dict(binding_hash=binding['binding_hash'],sources=binding['sources'],status='not_established',request_count=10,artifact_id='test-artifact',checks=[])
    monkeypatch.setattr(guided_http,'execute',execute)
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':True}).json())
    assert len(called)==1
    assert job['result']['requests_sent']==10
    assert job['result']['verification_executed'] is True
    assert job['steps'][3]['status']=='completed'
    assert job['result']['items'][0]['auto_verification']['status']=='not_established'
    duplicate=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':True}).json()
    assert duplicate['id']==job['id'] and len(called)==1
