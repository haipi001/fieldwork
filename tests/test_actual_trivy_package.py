"""Opt-in acceptance of unmodified real Trivy outputs and a fetched public OSV record."""
import hashlib
import json
import os
from pathlib import Path
import pytest
import final_core
import traditional_tools
import capability_registry
from tests.test_final import client
from tests.test_v5_intelligence import campaign

pytestmark=pytest.mark.skipif(os.getenv('FIELDWORK_REAL_TRIVY')!='1',reason='explicit real Trivy artifact acceptance')
ROOT=Path(__file__).resolve().parents[1]/'build/actual-trivy-package-20261017'


@pytest.mark.parametrize('case',['positive','fixed','unrelated'])
def test_real_trivy_to_independent_package_receipt(client,case):
    raw=(ROOT/f'{case}.json').read_text()
    result=json.loads(raw)
    packages=[p for r in result['Results'] for p in r.get('Packages',[])]
    assert len(packages)==1
    package=packages[0]
    expected={'positive':('requests','2.31.0'),'fixed':('requests','2.32.5'),'unrelated':('idna','3.10')}
    assert (package['Name'],package['Version'])==expected[case]
    advisory=json.loads((ROOT/'advisory.json').read_text())
    assert advisory['id']=='GHSA-9wx4-h78v-vm56'
    engagement,cid=campaign(client)
    imported=client.post('/api/v1/intelligence/records',json={'adapter':'osv','source_name':'osv-public-acceptance-20261017',
        'license':'source terms not verified; local acceptance snapshot only',
        'source_url':'https://api.osv.dev/v1/vulns/GHSA-9wx4-h78v-vm56','records':[advisory]})
    assert imported.status_code==201,imported.text
    run='actual-trivy-'+case
    now=final_core.utcnow()
    with final_core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(run,engagement['id'],'traditional',
            engagement['current_scope_snapshot_id'],engagement['current_policy_id'],'completed','report',0,now,None,None,now,now))
    observations=traditional_tools.parse_output('trivy',raw)
    assert any(x['observation_type']=='code.dependency_inventory' for x in observations)
    persisted=traditional_tools._persist_result({'id':run,'engagement_id':engagement['id'],'mode':'traditional'},'trivy',
        capability_registry.ToolResultEnvelope('trivy','completed',0,raw,(ROOT/f'{case}.log').read_text(),False),observations,
        ['fs','--format','json','--scanners','vuln','--offline-scan','--disable-telemetry','--skip-java-db-update',*(['--skip-db-update'] if case!='positive' else []),'--timeout','90s' if case=='positive' else '45s',str(ROOT/case)])
    fingerprint_base=f"/api/v1/intelligence/campaigns/{cid}/artifacts/{persisted['artifact_id']}"
    fingerprint_plan=client.get(fingerprint_base+'/fingerprint-plan')
    assert fingerprint_plan.status_code==200,fingerprint_plan.text
    assert fingerprint_plan.json()['inventory_mode']=='package_inventory'
    assert [(p['name'],p['version']) for p in fingerprint_plan.json()['packages']]==[expected[case]]
    match_response=client.post(fingerprint_base+'/fingerprint',json={'preview_hash':fingerprint_plan.json()['preview_hash']})
    assert match_response.status_code==200,match_response.text
    matches=match_response.json()['matches']
    receipt=None
    if case=='unrelated':assert matches==[]
    else:
        assert len(matches)==1
        match=matches[0];base=f"/api/v1/intelligence/matches/{match['id']}"
        plan=client.get(base+'/verification-plan')
        assert plan.status_code==200,plan.text
        created=client.post(base+'/verification-request',json={'preview_hash':plan.json()['preview_hash']})
        assert created.status_code==202,created.text
        tick=client.post('/api/v1/verification/local/tick',params={'request_id':created.json()['request']['id']})
        assert tick.status_code==200,tick.text
        completed=tick.json()['completed'];assert len(completed)==1
        receipt=client.get('/api/v1/verification/receipts/'+completed[0]['receipt_id']).json()
        assert receipt['result']['status']==('verified' if case=='positive' else 'inconclusive')
        assert receipt['environment']['process_isolation']['network_denied'] is True
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM canonical_findings').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0]==0
        artifact=dict(db.execute('SELECT * FROM artifacts WHERE id=?',(persisted['artifact_id'],)).fetchone())
    (ROOT/f'{case}-persisted-artifact.json').write_bytes(Path(artifact['uri']).read_bytes())
    chosen=[v for r in result['Results'] for v in r.get('Vulnerabilities',[]) if v['VulnerabilityID']=='CVE-2024-35195']
    (ROOT/f'{case}-acceptance.json').write_text(json.dumps({'case':case,'package':package,'selected_advisory_scanner_matches':len(chosen),
        'raw_stdout_sha256':hashlib.sha256(raw.encode()).hexdigest(),'artifact_sha256':artifact['sha256'],'receipt':receipt,
        'match_count':len(matches),'canonical_findings':0,'model_calls':0},ensure_ascii=False,indent=2))
    assert len(chosen)==(1 if case=='positive' else 0)
