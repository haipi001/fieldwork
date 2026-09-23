import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import reporting


@pytest.mark.parametrize('platform', ['markdown', 'json', 'html', 'sarif'])
def test_all_archive_members_redacted_and_unique(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path)
    model = {'id': 'proof-test', 'title': 'Evidence', 'evidence': [],
             'nested': [{'Authorization': 'Bearer SECRET_A', 'refresh_token': 'SECRET_B'}],
             'url': 'https://example.test/?token=SECRET_C',
             'summary': 'Cookie: sid=SECRET_D; session=SECRET_E'}
    content = json.dumps(model) if platform in {'json', 'sarif'} else model['summary']
    path, manifest = reporting.export_bundle('test', model, platform, content, {'ready': False},
                                            {'evidence/request.json': {'headers': {'cookie': 'SECRET_F'}}})
    with zipfile.ZipFile(path) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert set(archive.namelist()) == set(manifest['files'])
        for name in archive.namelist():
            assert b'SECRET_' not in archive.read(name)
        assert json.loads(archive.read('report.json'))['nested'][0]['Authorization'] == '[REDACTED]'
    assert model['nested'][0]['refresh_token'] == 'SECRET_B'
    assert reporting.verify_bundle(Path(path))['integrity_ok']
    assert manifest['sha256'] == hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_proof_verifier_rejects_tampered_or_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path)
    path, _ = reporting.export_bundle('proof', {'id':'f', 'evidence':[]}, 'markdown', 'original', {'ready':False})
    with zipfile.ZipFile(path) as source:
        contents = {n: source.read(n) for n in source.namelist()}
    contents['report.md'] = b'changed'
    with zipfile.ZipFile(path, 'w') as target:
        for name, data in contents.items():
            target.writestr(name, data)
    with pytest.raises(ValueError, match='checksum mismatch'):
        reporting.verify_bundle(Path(path))
    del contents['report.md']
    with zipfile.ZipFile(path, 'w') as target:
        for name, data in contents.items():
            target.writestr(name, data)
    with pytest.raises(ValueError, match='manifest'):
        reporting.verify_bundle(Path(path))


def test_proof_export_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path)
    with pytest.raises(ValueError):
        reporting.export_bundle('proof', {'id':'f','evidence':[]}, 'markdown', '', {'ready':False}, {'../outside':'data'})
    assert not (tmp_path.parent/'outside').exists()


def test_recorded_http_replay_contract_is_verified_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path)
    semantic = {"passed": True, "checks": {"distinct_principals": True, "owner_bound": True}}
    artifact = {
        "oracle": "http-authorization-read-v2", "reproduced": True, "stable": True,
        "rounds": [{"attack": {"status": 200}}, {"attack": {"status": 200}}],
        "semantic_checks": [semantic, semantic],
    }
    attachments = {
        "proof/evidence/artifact-http.json": artifact,
        "proof/replay-contract.json": {
            "schema": "fieldwork-replay-contract/1", "oracle": "http-authorization-read-v2",
            "mode": "recorded_assertion", "artifact_files": {"artifact-http": "proof/evidence/artifact-http.json"},
            "assertions": {"kind": "http_authorization_read_v2", "minimum_rounds": 2,
                           "require_reproduced": True, "require_stable": True,
                           "require_all_semantic_checks": True},
        },
    }
    path, _ = reporting.export_bundle('portable-http', {'id': 'f', 'title': 'HTTP proof', 'evidence': []},
                                      'markdown', 'proof', {'ready': True}, attachments)
    checked = reporting.replay_bundle(Path(path))
    assert checked["integrity_ok"] is True and checked["recorded_replay_verified"] is True
    assert checked["replay_kind"] == "http_authorization_read_v2"
    assert checked["execution"] == {"requested": False, "performed": False, "supported": False}


def test_recorded_replay_rejects_semantically_invalid_but_well_formed_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, 'EXPORTS', tmp_path)
    attachments = {
        "proof/evidence/artifact-http.json": {
            "oracle": "http-authorization-read-v2", "reproduced": False, "stable": True,
            "rounds": [{}, {}], "semantic_checks": [{"passed": False}, {"passed": False}],
        },
        "proof/replay-contract.json": {
            "schema": "fieldwork-replay-contract/1", "artifact_files": {"a": "proof/evidence/artifact-http.json"},
            "assertions": {"kind": "http_authorization_read_v2", "minimum_rounds": 2},
        },
    }
    path, _ = reporting.export_bundle('invalid-replay', {'id': 'f', 'title': 'Invalid', 'evidence': []},
                                      'markdown', 'proof', {'ready': False}, attachments)
    with pytest.raises(ValueError, match="does not satisfy"):
        reporting.replay_bundle(Path(path))
