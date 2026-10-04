import pytest
from fastapi import HTTPException
import final_core as core
import guided_research as guided
import guided_http
from tests.test_final import client
from tests.test_guided_research import candidate,wait


def queue_fixture(client,monkeypatch,fail_first=False):
    run,c=candidate(client)
    with core.connect() as db:
        row=dict(db.execute('SELECT * FROM candidate_findings WHERE id=?',(c['id'],)).fetchone())
        columns=list(row)
        for i in range(6):
            clone={**row,'id':f'queue-{i}','title':f'Queue candidate {i}'}
            db.execute('INSERT INTO candidate_findings ('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[clone[k] for k in columns])
    original=guided.material_for
    def material(c,data):
        value=original(c,data);value['gaps']=[];value['draft']['http_binding']={'binding_hash':c['id']}
        return value
    monkeypatch.setattr(guided,'material_for',material)
    calls=[]
    def execute(c,binding,before,after,*,checkpoint_callback=None):
        calls.append(c['id'])
        if fail_first and c['id']==row['id']:raise HTTPException(409,'Fixture unavailable')
        for index in range(10):
            checkpoint_callback({"completed_responses": index + 1, "artifact_id": "fixture-checkpoint",
                                 "required_requests": 10, "state": "in_progress", "promotion_eligible": False})
            after()
        return {'status':'not_established','binding_hash':binding['binding_hash']}
    monkeypatch.setattr(guided_http,'execute',execute)
    return run,calls


def start(client,run,**fields):
    return wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':True,**fields}).json())


def test_main_continuation_finishes_seven_without_repeating_completed(client,monkeypatch):
    run,calls=queue_fixture(client,monkeypatch)
    first=start(client,run)
    assert first['result']['new_verifications']==3 and first['result']['pending_verifications']==4
    second=start(client,run,continue_from=first['id'])
    assert second['result']['new_verifications']==3 and second['result']['reused_verifications']==3
    assert second['result']['pending_verifications']==1 and second['result']['requests_sent']==30
    with core.connect() as db:
        db.execute('UPDATE observations SET summary=? WHERE run_id=?',('New evidence arrived after continuation',run['id']))
    assert start(client,run,continue_from=first['id'])['id']==second['id']
    third=start(client,run,continue_from=second['id'])
    assert third['result']['new_verifications']==1 and third['result']['pending_verifications']==0
    assert len(calls)==len(set(calls))==7
    assert client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':True,'continue_from':third['id']}).status_code==409


def test_failed_candidate_does_not_starve_remaining_queue(client,monkeypatch):
    run,calls=queue_fixture(client,monkeypatch,fail_first=True)
    job=start(client,run)
    for _ in range(2):job=start(client,run,continue_from=job['id'])
    assert job['result']['pending_verifications']==0
    assert len(calls)==len(set(calls))==7
    assert any(g['code']=='verification_failed' for item in job['result']['items'] for g in item['gaps'])
    assert start(client,run)['id']==job['id']
    assert len(calls)==7


def test_continuation_cannot_cross_runs_or_change_batch(client,monkeypatch):
    run,calls=queue_fixture(client,monkeypatch)
    job=start(client,run)
    other,_=candidate(client)
    assert client.post(f"/api/v1/runs/{other['id']}/guided-research",json={'execute_ready':True,'continue_from':job['id']}).status_code==409
    assert client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'continue_from':job['id']}).status_code==409
