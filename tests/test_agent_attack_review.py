"""Review actual isolated runs without issuing new execution or findings."""
import copy
import pytest
import final_core as core
from tests.test_final import client
from tests.test_agent_attack_dispatch import setup as dispatch_setup
from tests.test_agent_attack_adapters import SOURCE
from tests.test_agent_attack_executor import tool_adapter_source
from agent_attack_review import summarize


def observed(client, category='injection', source=SOURCE):
    audit,agent,url,inputs,plan=dispatch_setup(client,source,category)
    run=client.post(url+'/execute',json={**inputs,'plan_hash':plan['plan_hash'],'confirmed':True})
    assert run.status_code==200,run.text
    evidence=client.get(url+'/evidence');assert evidence.status_code==200,evidence.text
    return agent,url,evidence.json()


@pytest.mark.parametrize('category,source,outcome',[
    ('injection',SOURCE,'not_observed_in_supported_scope'),
    ('injection','import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"response":v["system"] if "Ignore" in v["untrusted_document"] else "Refused"}))','signal_observed'),
    ('injection','import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps({"response":v["system"]}))','control_not_clean'),
    ('memory',SOURCE,'not_observed_in_supported_scope'),
    ('memory','import json,sys,pathlib\nv=json.load(sys.stdin)\np=pathlib.Path(v["memory"]["file_path"])\nif "Ignore" in v["untrusted_document"]: p.write_text("{}")\nprint(json.dumps({"response":"Reviewed"}))','signal_observed'),
    ('tool',tool_adapter_source('protected'),'not_observed_in_supported_scope'),
    ('tool',tool_adapter_source('forbidden_resource'),'signal_observed'),
])
def test_review_uses_frozen_inputs_and_measured_outputs(client,category,source,outcome):
    agent,url,evidence=observed(client,category,source)
    with core.connect() as db:
        before={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('analysis_runs','runtime_calls','canonical_findings','scope_snapshots')}
    response=client.get(url+'/review');assert response.status_code==200,response.text
    value=response.json()
    assert value['review_outcome']==outcome
    assert value['run_id']==evidence['run_id'] and value['authorization_current']
    assert not value['independently_verified'] and not value['finding_issued']
    assert all(row['input_verified'] for row in value['cases'])
    with core.connect() as db:
        assert before=={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
        db.execute("UPDATE agent_registry_v5 SET name='Changed since authorization' WHERE id=?",(agent,))
    historical=client.get(url+'/review');assert historical.status_code==200,historical.text
    assert not historical.json()['authorization_current']


def test_legacy_and_incomplete_records_are_unknown_and_corrupt_capsules_fail(client):
    _,_,evidence=observed(client)
    old=copy.deepcopy(evidence)
    for case in old['observation']['result']['cases']:case.pop('input_capsule')
    assert summarize(old)['review_outcome']=='legacy_input_unavailable'
    partial=copy.deepcopy(evidence);partial['observation']['result']['cases'].pop()
    assert summarize(partial)['review_outcome']=='incomplete'
    corrupt=copy.deepcopy(evidence);corrupt['observation']['result']['cases'][1]['input_capsule']['system']='changed'
    with pytest.raises(ValueError,match='input capsule digest'):summarize(corrupt)
    corrupt=copy.deepcopy(evidence);corrupt['observation']['result']['cases'][1]['stdout']='changed'
    with pytest.raises(ValueError,match='output digest'):summarize(corrupt)
