import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import prepare_native_release as release


def configure(monkeypatch):
    monkeypatch.setattr(release.platform,'system',lambda:'Darwin')
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if 'clang' in args:Path(args[-1]).write_bytes(b'fixture binary')
        output=b'{"synthetic":true}\n{"synthetic":true}\n' if '--self-test' in args else b''
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(release.subprocess,'run',run)
    return calls


def test_unsigned_preview_has_hash_and_never_claims_installation_ready(tmp_path,monkeypatch):
    calls=configure(monkeypatch);output=tmp_path/'preview.zip'
    manifest=release.prepare(output)
    assert manifest['signing']=='unsigned_preview'
    assert manifest['installation_ready'] is False and manifest['live_collection_verified'] is False
    assert not any('/usr/bin/codesign' in args for args in calls)
    with zipfile.ZipFile(output) as archive:
        binary=archive.read('com.fieldwork.native-ai-collector')
        assert hashlib.sha256(binary).hexdigest()==manifest['binary_sha256']
        assert json.loads(archive.read('manifest.json'))==manifest
    with pytest.raises(ValueError,match='已存在'):release.prepare(output)
    assert output.exists()


def test_publisher_mode_must_pass_pinned_signature_verification(tmp_path,monkeypatch):
    calls=configure(monkeypatch);checked=[]
    monkeypatch.setattr(release,'verify_signature',lambda path,team:checked.append(team))
    manifest=release.prepare(tmp_path/'signed.zip',team_id='ABCDEFGHIJ',sign_identity='fixture publisher')
    assert manifest['signing']=='publisher_verified' and checked==['ABCDEFGHIJ']
    assert any('--identifier' in args for args in calls)
    def rejected(*args):raise ValueError('fixture signature failure')
    monkeypatch.setattr(release,'verify_signature',rejected)
    output=tmp_path/'bad.zip'
    with pytest.raises(ValueError):release.prepare(output,team_id='ABCDEFGHIJ',sign_identity='fixture publisher')
    assert not output.exists()


def test_missing_identity_and_ad_hoc_signing_are_rejected(tmp_path,monkeypatch):
    configure(monkeypatch)
    with pytest.raises(ValueError):release.prepare(tmp_path/'x.zip',team_id='ABCDEFGHIJ')
    with pytest.raises(ValueError):release.prepare(tmp_path/'x.zip',team_id='ABCDEFGHIJ',sign_identity='-')


def test_build_failure_never_publishes_output(tmp_path,monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(release.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b''))
    output=tmp_path/'bad.zip'
    with pytest.raises(ValueError):release.prepare(output)
    assert not output.exists()
