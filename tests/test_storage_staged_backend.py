"""Full-schema migration staging and fresh-process backend acceptance, isolated data only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from storage_staging import stage, verify_stage

ROOT = Path(__file__).resolve().parents[1]


def backend(directory, mode):
    code = r'''
import hashlib, json, sqlite3, sys, zipfile
from pathlib import Path
from fastapi.testclient import TestClient
import app, final_core
from tests.test_v5_team_plan import _setup
root = app.DATA
assert str(root) == sys.argv[1]
with TestClient(app.app, base_url='http://127.0.0.1:8000',headers={'X-Fieldwork-Session':'isolated-migration-session-token-12345678901234567890'}) as client:
    if sys.argv[2] == 'seed':
        engagement,campaign,run_id = _setup(client)
        artifact = root/'artifacts'/'fixture.bin'; artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(b'isolated-evidence')
        with final_core.connect() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',('migration-artifact',run_id,'fixture',str(artifact),hashlib.sha256(artifact.read_bytes()).hexdigest(),'application/octet-stream',1,final_core.utcnow()))
        report = root/'exports'/'fixture.zip';report.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(report,'w') as output: output.writestr('report.txt','isolated report')
        with sqlite3.connect(app.DB) as db:
            db.execute('INSERT INTO submission_packages_v2 VALUES(?,?,?,?,?,?,?,?)',('migration-package','fixture-finding','generic','draft_incomplete','{}',str(report),final_core.utcnow(),final_core.utcnow()))
    else:
        assert client.get('/v5').status_code == 200
        settings = client.get('/api/v1/workspace/maintenance')
        assert settings.status_code == 200, settings.text
        assert settings.json()['data_directory'] == str(root)
        relation = client.get('/api/v1/workspace/artifacts/migration-artifact/relations')
        assert relation.status_code == 200, relation.text
        download = client.get('/api/v1/submission-packages/migration-package/download')
        assert download.status_code == 200, download.text
        assert download.content == (root/'exports/fixture.zip').read_bytes()
        backup = client.post('/api/v1/workspace/evidence-backups')
        assert backup.status_code == 201, backup.text
        with final_core.connect() as db:
            row = db.execute('SELECT uri,sha256 FROM artifacts WHERE id=?',('migration-artifact',)).fetchone()
            assert Path(row['uri']) == root/'artifacts/fixture.bin'
            assert hashlib.sha256(Path(row['uri']).read_bytes()).hexdigest() == row['sha256']
            assert db.execute('SELECT count(*) FROM runtime_calls').fetchone()[0] == 0
print(json.dumps({'mode':sys.argv[2],'accepted':True}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(directory), mode], cwd=ROOT,
                            env=dict(os.environ, FIELDWORK_DATA_DIRECTORY=str(directory),
                                     FIELDWORK_DISABLE_AGENT_MONITOR='1', FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER='1',
                                     FIELDWORK_SESSION_TOKEN='isolated-migration-session-token-12345678901234567890'),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def test_staged_full_schema_backend_restarts_and_reads_evidence_and_report(tmp_path):
    source=tmp_path.resolve()/'source';source.mkdir(mode=0o700)
    target=tmp_path.resolve()/'target';target.mkdir(mode=0o700)
    backend(source,'seed')
    original=hashlib.sha256((source/'src_control.db').read_bytes()).hexdigest()
    result=stage(source,target)
    staged=Path(result['stage_directory'])/'data'
    assert result['reference_relocation']['changed_references']==2
    assert verify_stage(Path(result['stage_directory']))['verified']
    assert backend(staged,'accept')['accepted']
    assert backend(staged,'accept')['accepted']
    with pytest.raises(ValueError, match='staged_files_changed'):
        verify_stage(Path(result['stage_directory']))
    assert hashlib.sha256((source/'src_control.db').read_bytes()).hexdigest()==original
    assert not result['activation_allowed']
    assert backend(source,'accept')['accepted']  # original directory remains usable for rollback
