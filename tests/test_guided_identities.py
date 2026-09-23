import pytest
import final_core
import guided_research as guided
from tests.test_final import client, create_ready
from tests.test_guided_research import candidate, wait


def authorized(client):
    created = client.post('/api/v1/engagements', json={'name':'Account fixture','target':'https://accounts.test','mode':'traditional','scope':{'allow_authentication':True},'policy':{}}).json()
    return client.post(f"/api/v1/engagements/{created['id']}/confirm").json()


def test_quick_accounts_authorized_idempotent_and_not_ready(client):
    denied=create_ready(client)
    assert client.post(f"/api/v1/engagements/{denied['id']}/quick-identities").status_code==409
    project=authorized(client)
    path=f"/api/v1/engagements/{project['id']}/quick-identities"
    first=client.post(path)
    assert first.status_code==200
    rows=first.json()['identities']
    assert len(rows)==2 and all(x['session_status']=='needs_login' and not x['credential_configured'] for x in rows)
    assert {x['id'] for x in client.post(path).json()['identities']}=={x['id'] for x in rows}
    assert all('credential_ref' not in x for x in rows)


@pytest.mark.parametrize('enabled',[False,True])
def test_identity_change_resumes_only_opted_in_waiting_job(client,monkeypatch,enabled):
    run,c=candidate(client)
    job=wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':enabled}).json())
    project=final_core.get_run(run['id'])['engagement_id']
    identity=client.post(f'/api/v1/engagements/{project}/identities',json={'label':'Account','role':'user'}).json()
    calls=[]
    monkeypatch.setattr(guided,'dispatch',lambda *args:calls.append(args))
    resumed=guided.resume_after_identity(identity['id'])
    assert bool(resumed)==enabled and bool(calls)==enabled
    assert client.get(f"/api/v1/guided-research/{job['id']}").json()['status']=='awaiting_input'
    if enabled:
        assert resumed[0]!=job['id']
        # An active successor prevents additional login events from creating work.
        assert guided.resume_after_identity(identity['id'])==[]


def test_login_other_project_does_not_resume(client,monkeypatch):
    run,c=candidate(client)
    wait(client,client.post(f"/api/v1/runs/{run['id']}/guided-research",json={'execute_ready':True}).json())
    other=authorized(client)
    identity=client.post(f"/api/v1/engagements/{other['id']}/quick-identities").json()['identities'][0]
    monkeypatch.setattr(guided,'dispatch',lambda *args:pytest.fail('unrelated task resumed'))
    assert guided.resume_after_identity(identity['id'])==[]
