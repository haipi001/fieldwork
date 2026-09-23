from concurrent.futures import ThreadPoolExecutor
import pytest
import final_core
import guided_research as guided
from tests.test_final import client, create_ready
from tests.test_guided_research import candidate, wait


def configured(client, enabled=True, status='completed', synthetic=False):
    run,_=candidate(client)
    with final_core.connect() as db:
        db.execute('UPDATE analysis_runs SET status=?,synthetic=? WHERE id=?',(status,int(synthetic),run['id']))
        db.execute('INSERT INTO run_configs_v2 VALUES(?,?,?)',(run['id'],final_core.dump({'guided_followup':enabled}),final_core.utcnow()))
    return run


def test_completed_run_continues_without_browser_and_survives_restart(client,monkeypatch):
    import guided_pages
    monkeypatch.setattr(guided_pages,'collect',lambda *args:[])
    run=configured(client)
    guided.init_guided_db()
    monkeypatch.setattr(guided,'dispatch',guided.run_job)
    ids=guided.start_completed_followups()
    assert len(ids)==1
    job=guided.get_job(ids[0])
    assert job['run_id']==run['id'] and job['status']=='awaiting_input'
    assert job['result']['execute_ready'] is True
    assert len(job['steps'])==5 and job['result']['items']
    assert guided.start_completed_followups()==[]


@pytest.mark.parametrize('enabled,status,synthetic',[(False,'completed',False),(True,'running',False),(True,'paused',False),(True,'stopped',False),(True,'budget_exhausted',False),(True,'completed',True)])
def test_only_explicit_real_completed_runs_follow_up(client,monkeypatch,enabled,status,synthetic):
    configured(client,enabled,status,synthetic)
    monkeypatch.setattr(guided,'dispatch',lambda *args:pytest.fail('ineligible run dispatched'))
    assert guided.start_completed_followups()==[]


@pytest.mark.parametrize('status',['failed','interrupted','cancelled'])
def test_terminal_job_is_not_automatically_replayed(client,monkeypatch,status):
    run=configured(client)
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    ids=guided.start_completed_followups()
    with final_core.connect() as db: db.execute('UPDATE guided_research_jobs SET status=? WHERE id=?',(status,ids[0]))
    assert guided.start_completed_followups()==[]


def test_concurrent_followups_dispatch_once(client,monkeypatch):
    run=configured(client)
    calls=[]
    monkeypatch.setattr(guided,'dispatch',lambda *args:calls.append(args))
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _:guided.start_completed_followups(),range(2)))
    assert len(calls)==1
    with final_core.connect() as db: assert db.execute('SELECT count(*) FROM guided_research_jobs WHERE run_id=?',(run['id'],)).fetchone()[0]==1


def test_capacity_retains_intent_for_next_tick(client,monkeypatch):
    runs=[configured(client) for _ in range(5)]
    monkeypatch.setattr(guided,'dispatch',lambda *args:None)
    first=guided.start_completed_followups()
    assert len(first)==4
    assert guided.start_completed_followups()==[]
    with final_core.connect() as db: db.execute("UPDATE guided_research_jobs SET status='completed' WHERE id=?",(first[0],))
    assert len(guided.start_completed_followups())==1


def test_launch_persists_guided_intent_in_same_transaction(client,monkeypatch):
    import traditional_tools
    async def no_execution(*args): pass
    monkeypatch.setattr(traditional_tools,'execute_toolchain',no_execution)
    project=create_ready(client)
    result=client.post(f"/api/v1/engagements/{project['id']}/start",json={'execution_mode':'real','guided_followup':True})
    assert result.status_code==202
    with final_core.connect() as db:
        row=db.execute('SELECT config FROM run_configs_v2 WHERE run_id=?',(result.json()['id'],)).fetchone()
    assert final_core.load(row['config'],{})['guided_followup'] is True
