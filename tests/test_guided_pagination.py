import pytest
import final_core as core
import guided_research as guided
from tests.test_final import client
from tests.test_guided_research import candidate,wait


def many(client):
    run,first=candidate(client)
    with core.connect() as db:
        row=dict(db.execute('SELECT * FROM candidate_findings WHERE id=?',(first['id'],)).fetchone())
        columns=list(row)
        for i in range(204):
            clone={**row,'id':f'candidate-page-{i:04d}','title':f'Page fixture {i}'}
            db.execute('INSERT INTO candidate_findings ('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[clone[k] for k in columns])
    return run


def test_all_candidates_reachable_in_three_batches_with_stable_cursor(client):
    run=many(client);cursor=None;seen=[];jobs=[]
    for expected_remaining in (105,5,0):
        body={'after_candidate_id':cursor}
        job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json=body).json())
        jobs.append(job)
        assert job['result']['remaining_candidates']==expected_remaining
        ids=[x['candidate_id'] for x in job['result']['items']]
        assert not set(ids).intersection(seen)
        seen.extend(ids)
        repeated=client.post(f"/api/v1/runs/{run['id']}/guided-research",json=body).json()
        assert repeated['id']==job['id'] and repeated['reused']
        cursor=job['result']['next_cursor']
    assert len(seen)==205 and cursor is None
    for job in jobs:
        assert client.get(f"/api/v1/guided-research/{job['id']}").json()['result']==job['result']


def test_archiving_earlier_page_does_not_skip_later_candidates(client):
    run=many(client)
    first=guided.snapshot(run['id']);cursor=first['next_cursor']
    with core.connect() as db:db.execute("UPDATE candidate_findings SET status='archived' WHERE run_id=? AND id<=?",(run['id'],cursor))
    second=guided.snapshot(run['id'],after_candidate_id=cursor)
    assert len(second['candidates'])==100 and second['remaining_candidates']==5
    assert all(x['id']>cursor for x in second['candidates'])


def test_cursor_rejects_other_run_and_single_candidate_conflict(client):
    run,c=candidate(client);other,d=candidate(client)
    path=f"/api/v1/runs/{run['id']}/guided-research"
    assert client.post(path,json={'after_candidate_id':d['id']}).status_code==409
    assert client.post(path,json={'after_candidate_id':c['id'],'candidate_id':c['id']}).status_code==422


def test_single_candidate_job_does_not_replace_batch_cursor(client):
    run=many(client)
    batch=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={}).json())
    single=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'candidate_id':batch['result']['items'][0]['candidate_id']}).json())
    assert single['id']!=batch['id']
    restored=client.get(f"/api/v1/runs/{run['id']}/guided-research?batch_only=true").json()
    assert restored['id']==batch['id'] and restored['result']['next_cursor']


def test_active_single_job_still_exposes_stop_from_main_panel(client,monkeypatch):
    run,c=candidate(client)
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    job=client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'candidate_id':c['id']}).json()
    active=client.get(f"/api/v1/runs/{run['id']}/guided-research?batch_only=true").json()
    assert active['id']==job['id'] and active['status']=='queued'


def test_selected_batch_direct_sources_take_priority_over_global_history(client):
    run,c=candidate(client)
    with core.connect() as db:
        original=dict(db.execute('SELECT * FROM observations WHERE run_id=?',(run['id'],)).fetchone())
        columns=list(original)
        for i in range(1100):
            value={**original,'id':f'000-old-{i:04d}','summary':'Unrelated old material','subject':'https://example.test/unrelated'}
            db.execute('INSERT INTO observations ('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[value[k] for k in columns])
    data=guided.snapshot(run['id'],c['id'])
    item=guided.material_for(data['candidates'][0],data)
    assert original['id'] in {x['id'] for x in data['observations']}
    assert item['draft']['observed_behavior']==original['summary']
    assert data['truncated'] is True
