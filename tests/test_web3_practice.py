import hashlib
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import web3_practice as practice


@pytest.mark.skipif(not practice.binary('forge'), reason='Forge not installed')
def test_economic_controls_run_and_export_real_source(tmp_path, monkeypatch):
    monkeypatch.setattr(practice, 'RESULTS', tmp_path / 'results')
    app = FastAPI()
    app.include_router(practice.router)
    client = TestClient(app)
    response = client.post('/api/v1/web3/practice/run')
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['status'] == 'passed', report
    assert len(report['rounds']) == 2
    assert {r['seed'] for r in report['rounds']} == set(practice.SEEDS)
    assert all(len(r['tests']) == 8 for r in report['rounds'])
    archive = client.get(f"/api/v1/web3/practice/results/{report['id']}/download")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        assert json.loads(package.read('report.json'))['status'] == 'passed'
        for name, digest in report['source_hashes'].items():
            assert hashlib.sha256(package.read('source/' + name)).hexdigest() == digest
    assert client.get('/api/v1/web3/practice/results/not-an-id/download').status_code == 404


def test_empty_test_inventory_and_missing_forge_cannot_pass(monkeypatch):
    assert not practice.evaluate_round({}, 0)['passed']
    monkeypatch.setattr(practice, 'binary', lambda _: None)
    assert not practice.catalog()['available']
    with pytest.raises(Exception) as error:
        practice.run_practice()
    assert error.value.status_code == 409


def test_all_success_does_not_replace_economic_metrics():
    tests = {name: {'status': 'Success', 'kind': {'Fuzz': {'runs': 64}}} for name in practice.EXPECTED}
    result = practice.evaluate_round({'suite': {'test_results': tests}}, 0)
    assert result['checks']['all_assertions_passed']
    assert not result['passed']
    assert not result['checks']['inflation_fixed_economic_control']
