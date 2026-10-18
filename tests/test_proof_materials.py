import hashlib
import os

import pytest

import final_core as core
import proof_materials
from tests.test_historical_proof_shapes import client, historical, assert_review


def test_bounded_regular_proof_bytes(tmp_path):
    path = tmp_path / 'proof.json'
    path.write_bytes(b'{"result":"fixture"}')
    assert proof_materials.read_proof_file(path, path.stat().st_size) == path.read_bytes()
    with pytest.raises(ValueError):
        proof_materials.read_proof_file(path, 2)


@pytest.mark.parametrize('kind', ['leaf_link', 'parent_link', 'hardlink', 'fifo'])
def test_proof_reader_rejects_nonplain_material(tmp_path, kind):
    root = tmp_path / 'root'; root.mkdir()
    actual = root / 'actual'; actual.write_bytes(b'proof')
    path = root / 'selected'
    if kind == 'leaf_link':
        path.symlink_to(actual)
    elif kind == 'parent_link':
        linked = tmp_path / 'linked'; linked.symlink_to(root, target_is_directory=True)
        path = linked / 'actual'
    elif kind == 'hardlink':
        os.link(actual, path)
    else:
        os.mkfifo(path)
    with pytest.raises((ValueError, OSError)):
        proof_materials.read_proof_file(path, 100)


@pytest.mark.parametrize('change', ['append', 'replace'])
def test_file_change_during_read_is_rejected(tmp_path, monkeypatch, change):
    path = tmp_path / 'proof'; path.write_bytes(b'original')
    original = os.read
    changed = False
    def read(descriptor, size):
        nonlocal changed
        data = original(descriptor, size)
        if not changed:
            changed = True
            if change == 'append':
                with path.open('ab') as stream:
                    stream.write(b'changed')
            else:
                other = tmp_path / 'replacement'; other.write_bytes(b'original')
                other.replace(path)
        return data
    monkeypatch.setattr(proof_materials.os, 'read', read)
    with pytest.raises(ValueError, match='changed'):
        proof_materials.read_proof_file(path, 100)


@pytest.mark.parametrize('kind', ['plain', 'leaf_link', 'parent_link'])
def test_capsule_and_stage_use_link_safe_artifact_reader(client, historical, tmp_path, kind):
    root = tmp_path / 'materials'; root.mkdir()
    actual = root / 'actual.json'; raw = b'{"fixture":true}'; actual.write_bytes(raw)
    path = actual
    if kind == 'leaf_link':
        path = root / 'linked.json'; path.symlink_to(actual)
    elif kind == 'parent_link':
        linked = tmp_path / 'linked'; linked.symlink_to(root, target_is_directory=True)
        path = linked / 'actual.json'
    digest = hashlib.sha256(raw).hexdigest()
    with core.connect() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)',
                   ('shape-artifact', historical, 'fixture', str(path), digest, 'application/json', 1, core.utcnow()))
        db.execute('UPDATE verification_attempts SET result=? WHERE id=?',
                   (core.dump({'artifacts': {'shape-artifact': digest}}), 'shape-receipt'))
    if kind == 'plain':
        response = client.get('/api/v1/findings/shape-finding/proof-capsule')
        assert response.status_code == 200, response.text
        assert response.json()['portable'] is False  # fixture oracle is deliberately unsupported
    else:
        assert_review(client, historical)
