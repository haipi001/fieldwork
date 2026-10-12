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


def test_pack_preflight_is_read_only_and_rejects_stale_manifests(client):
    import final_core as core
    before=core.DB.read_bytes()
    for pack in list_packs():
        url='/api/v1/workspace/packs/'+pack['id']+'/preflight'
        response=client.post(url,json={'manifest_sha256':pack['manifest_sha256']})
        assert response.status_code==200,response.text
        value=response.json()
        assert value['implementations_present']
        assert value['checks'] and all(len(c['source_sha256'])==64 for c in value['checks'])
        assert not value['execution_authorized'] and not value['installed_external_code']
        assert value['scope']=='static_source_presence_only'
        assert client.post(url,json={'manifest_sha256':'f'*64}).status_code==409
        assert client.post(url,json={'manifest_sha256':pack['manifest_sha256'],'command':'shell'}).status_code==422
    assert client.post('/api/v1/workspace/packs/unknown/preflight',json={'manifest_sha256':'a'*64}).status_code==404
    assert core.DB.read_bytes()==before


def test_pack_preflight_reports_missing_source_without_importing(client,monkeypatch):
    import v6_packs
    from pathlib import Path
    original=Path.read_bytes
    def unavailable(path):
        if path.name=='traditional_runtime.py':raise OSError('fixture missing source')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',unavailable)
    pack=list_packs()[0]
    response=client.post('/api/v1/workspace/packs/src/preflight',json={'manifest_sha256':pack['manifest_sha256']})
    assert response.status_code==200
    value=response.json()
    assert not value['implementations_present']
    missing=[c for c in value['checks'] if c['reference'].startswith('traditional_runtime:')]
    assert missing and all(not c['implementation_present'] and c['source_sha256'] is None for c in missing)
