import pytest
from fastapi import HTTPException
import guided_research as guided
import final_core as core
import traditional_runtime as http
from tests.test_final import client
from tests.test_guided_research import candidate,wait
from tests.test_guided_pages import prepare_page_run


def test_budget_and_run_state_change_invalidate_preparation_cache(client):
    run,c=candidate(client)
    path=f"/api/v1/runs/{run['id']}/guided-research"
    first=wait(client,client.post(path,json={}).json())
    with core.connect() as db:
        db.execute('INSERT INTO run_budgets_v2 VALUES(?,?,?,?,?,?,?,?)',(run['id'],30,0,20,0,100,0,core.utcnow()))
    second=wait(client,client.post(path,json={}).json())
    assert second['id']!=first['id']
    assert client.post(path,json={}).json()['id']==second['id']
    with core.connect() as db:db.execute("UPDATE analysis_runs SET status='paused' WHERE id=?",(run['id'],))
    third=wait(client,client.post(path,json={}).json())
    assert third['id']!=second['id']


def test_page_blocked_before_transport_can_continue_after_budget_change(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    path=f"/api/v1/runs/{run['id']}/guided-research"
    def blocked(*args):raise HTTPException(409,'fixture preflight denied')
    monkeypatch.setattr(http,'network_guard',blocked)
    first=wait(client,client.post(path,json={'read_pages':True}).json())
    assert first['result']['page_reads'][0]['attempted'] is False and not calls
    with core.connect() as db:db.execute('UPDATE run_budgets_v2 SET request_limit=21 WHERE run_id=?',(run['id'],))
    monkeypatch.setattr(http,'network_guard',lambda *args:None)
    second=wait(client,client.post(path,json={'read_pages':True}).json())
    assert second['id']!=first['id'] and len(calls)==1
    assert second['result']['page_reads'][0]['status']=='recorded'


def test_uncertain_sent_request_is_not_repeated_after_budget_change(client,monkeypatch):
    run,calls=prepare_page_run(client,monkeypatch)
    def uncertain(spec):
        calls.append(spec)
        raise HTTPException(409,'fixture response unavailable')
    monkeypatch.setattr(http,'request_once',uncertain)
    path=f"/api/v1/runs/{run['id']}/guided-research"
    first=wait(client,client.post(path,json={'read_pages':True}).json())
    assert first['result']['page_reads'][0]['attempted'] is True and len(calls)==1
    with core.connect() as db:db.execute('UPDATE run_budgets_v2 SET request_limit=21 WHERE run_id=?',(run['id'],))
    second=wait(client,client.post(path,json={'read_pages':True}).json())
    assert second['id']!=first['id'] and len(calls)==1


def test_policy_context_records_hashes_not_rule_contents(client):
    run,c=candidate(client)
    before=guided.snapshot(run['id'])
    with core.connect() as db:
        policy=core.get_engagement(core.get_run(run['id'])['engagement_id'])['current_policy_id']
        row=db.execute('SELECT policy FROM execution_policies WHERE id=?',(policy,)).fetchone()
        value=core.load(row['policy'],{});value['max_requests_per_second']=2
        db.execute('UPDATE execution_policies SET policy=? WHERE id=?',(core.dump(value),policy))
    after=guided.snapshot(run['id'])
    assert guided.fingerprint(before)!=guided.fingerprint(after)
    assert 'allowed_targets' not in str(after['runtime_context'])
