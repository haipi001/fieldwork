"""Durable acceptance files must retain registered bytes and reject substitution."""
import hashlib
import json
from pathlib import Path
import pytest
import final_core as core
from scripts.archive_browser_matrix_evidence import archive
from tests.test_final import client
from tests.test_agent_attack_review import observed


@pytest.mark.parametrize('tamper',['bytes','outside_root','archive'])
def test_registered_evidence_archive_retains_bytes_and_rejects_tamper(client,tmp_path,tamper):
    _,_,evidence=observed(client)
    output=tmp_path/'matrix';output.mkdir()
    (output/'fixture-inputs.json').write_text(json.dumps([{'run_id':evidence['run_id']}]))
    result=archive(output,'fixture',core.LOCAL_DATA_ROOT)
    raw=(output/result['facts_file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==result['facts_sha256']
    facts=json.loads(raw);assert result['canonical_findings']==0 and result['artifact_count']==1
    artifact=facts['artifacts'][0]
    destination=output/artifact['file']
    assert hashlib.sha256(destination.read_bytes()).hexdigest()==evidence['sha256']
    with core.connect() as db:
        row=db.execute('SELECT * FROM artifacts WHERE id=?',(evidence['artifact_id'],)).fetchone()
        path=Path(row['uri'])
        if tamper=='bytes':path.write_bytes(b'changed')
        elif tamper=='outside_root':db.execute('UPDATE artifacts SET uri=? WHERE id=?',('/etc/hosts',row['id']))
        else:destination.write_bytes(b'changed')
    with pytest.raises(ValueError):archive(output,'fixture',core.LOCAL_DATA_ROOT)
