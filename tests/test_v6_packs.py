import importlib

from v6_packs import list_packs
from tests.test_final import client


def test_pack_manifests_reference_real_adapters_and_expose_partial_state(client):
    manifests = client.get('/api/v1/v6/packs').json()['packs']
    assert {item['id'] for item in manifests} == {'src', 'web3', 'agent'}
    for item in manifests:
        assert item['integration_status'] == 'partial' and item['unsupported']
        assert len(item['manifest_sha256']) == 64
        for reference in item['tool_adapters'] + item['verifiers']:
            module, name = reference.split(':')
            assert callable(getattr(importlib.import_module(module), name))
    manifests[0]['tool_adapters'].clear()
    assert list_packs()[0]['tool_adapters']
    assert client.post('/api/v1/v6/packs', json={}).status_code == 405
