from tests.test_final import client
from tests.test_v5_native_discovery import prepare
from v6_http_gateway import authorize_native_browser_read, finish_http_read


def test_runtime_summary_counts_existing_ledgers_and_containment(client):
    project = prepare(client, 'https://summary.example.test', None)
    before = client.get('/api/v1/v6/runtime-summary').json()
    assert before['tasks']['total'] == before['model_calls']['total'] == 0
    action = authorize_native_browser_read('native-discovery-run', project['id'], 'https://summary.example.test/')
    active = client.get('/api/v1/v6/runtime-summary').json()
    assert active['tasks'] == {'total':1,'states':{'running':1}}
    assert active['http_unsettled'] == 1 and active['runner_load'][0]['running_tasks'] == 1
    finish_http_read(action, response={'status':200,'body_sha256':'a'*64,'body_bytes':1})
    client.post('/api/v1/v6/runs/native-discovery-run/contain', json={'reason_code':'compromised'})
    complete = client.get('/api/v1/v6/runtime-summary').json()
    assert complete['tasks'] == {'total':1,'states':{'succeeded':1}}
    assert complete['http_receipts'] == {'total':1,'states':{'completed':1}}
    assert complete['http_unsettled'] == 0 and complete['contained_runs'] == 1
    assert complete['grants'] == {'issued':1,'revoked':1,'uses':1}
    assert complete['model_calls']['total'] == 0 and complete['runner_load'][0]['running_tasks'] == 0
