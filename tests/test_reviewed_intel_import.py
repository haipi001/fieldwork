import pytest
import final_core
from tests.test_final import client


def body():
    return {'adapter':'osv','source_name':'reviewed-local','license':'operator declaration; not verified',
        'source_url':'https://example.test/advisory','records':[{'id':'GHSA-LOCAL-1','affected':[
            {'package':{'ecosystem':'PyPI','name':'example-pkg'},'versions':['1.2.3']}]}]}


def test_reviewed_import_previews_without_writes_then_imports_idempotently(client):
    value=body();preview=client.post('/api/v1/intelligence/records/preview',json=value)
    assert preview.status_code==200,preview.text
    plan=preview.json();assert plan['source_terms_verified'] is False and plan['network_requests']==0
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_records').fetchone()[0]==0
    payload={**value,'preview_hash':plan['preview_hash']}
    first=client.post('/api/v1/intelligence/records/import-reviewed',json=payload)
    assert first.status_code==201 and first.json()['inserted']==1
    again=client.post('/api/v1/intelligence/records/import-reviewed',json=payload)
    assert again.status_code==201 and again.json()['duplicates']==1 and again.json()['inserted']==0


@pytest.mark.parametrize('change',['license','record','oversized'])
def test_reviewed_import_rejects_changes_and_oversized_body_without_writing(client,change):
    value=body();plan=client.post('/api/v1/intelligence/records/preview',json=value).json()
    if change=='license':value['license']='different declaration'
    elif change=='record':value['records'][0]['affected'][0]['versions']=['9.9.9']
    else:value['records'][0]['ignored_input']='x'*2000001
    response=client.post('/api/v1/intelligence/records/import-reviewed',json={**value,'preview_hash':plan['preview_hash']})
    assert response.status_code==(413 if change=='oversized' else 409)
    with final_core.connect() as db:assert db.execute('SELECT count(*) FROM intel_records').fetchone()[0]==0
