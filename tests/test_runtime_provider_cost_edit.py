import pytest
import final_core
from tests.test_final import client
from tests.test_v5_runtime import provider, healthy


def test_cost_edit_preserves_provider_and_invalidates_old_profile(client):
    item = provider(client, 'cost-edit', 'cloud', key='fixture-secret-key')
    healthy(item['id'])
    old = client.put('/api/v1/runtime/config', json={'name':'Bound cost', 'mode':'cloud',
        'config':{'cloud_provider_ids':[item['id']]}}).json()
    with final_core.connect() as db:
        before = dict(db.execute('SELECT * FROM runtime_providers WHERE id=?', (item['id'],)).fetchone())
    response = client.patch('/api/v1/runtime/providers/'+item['id'], json={'cost_micros_per_million_tokens':1200000})
    assert response.status_code == 200
    assert response.json()['last_health'] == 'healthy'
    assert response.json()['metadata']['cost_micros_per_million_tokens'] == 1200000
    with final_core.connect() as db:
        after = dict(db.execute('SELECT * FROM runtime_providers WHERE id=?', (item['id'],)).fetchone())
    for key in ('base_url','model','secret_ref','last_health','last_health_at','enabled'):
        assert before[key] == after[key]
    import json
    before_meta, after_meta = json.loads(before['metadata_json']), json.loads(after['metadata_json'])
    before_meta['cost_micros_per_million_tokens'] = 1200000
    assert before_meta == after_meta
    fetched = client.get('/api/v1/runtime/profiles/'+old['id']).json()
    assert fetched['provider_configuration_status']=='stale'
    assert fetched['provider_configuration_current'] is False
    assert fetched['changed_provider_ids']==[item['id']]
    assert fetched['config']==old['config']
    assert fetched['config']['provider_config_hashes'][item['id']] != response.json()['configuration_sha256']
    route=client.post('/api/v1/runtime/routes',json={'task_type':'research','profile_id':old['id'],'sensitivity':'public','budget_remaining_micros':1000000}).json()
    assert route['status']=='blocked'
    assert 'provider_configuration_changed' in str(route['reason'])
    new = client.put('/api/v1/runtime/config', json={'name':'Bound cost', 'mode':'cloud',
        'config':{'cloud_provider_ids':[item['id']]}}).json()
    assert new['id'] != old['id']
    assert new['provider_configuration_current'] is True
    assert new['changed_provider_ids']==[]
    listed=client.get('/api/v1/runtime/config').json()['profiles']
    assert {x['id']:x['provider_configuration_status'] for x in listed}=={old['id']:'stale',new['id']:'current'}
    with final_core.connect() as db:
        db.execute('DELETE FROM runtime_providers WHERE id=?',(item['id'],))
    assert client.get('/api/v1/runtime/profiles/'+new['id']).json()['provider_configuration_status']=='stale'


@pytest.mark.parametrize('value', [-1, 10000000001, 1.5, True, '100'])
def test_cost_edit_rejects_invalid_values(client,value):
    item=provider(client,'invalid-cost','cloud')
    response=client.patch('/api/v1/runtime/providers/'+item['id'],json={'cost_micros_per_million_tokens':value})
    assert response.status_code == 422
    assert client.get('/api/v1/runtime/providers/'+item['id']).json()['metadata']['cost_micros_per_million_tokens']==1000000
