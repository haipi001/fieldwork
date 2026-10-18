import hashlib
import os
import pytest
import proof_materials
import traditional_tools
from v5_verification import _read_trivy_artifact


def fixture(tmp_path,monkeypatch):
    root=tmp_path/'artifacts';root.mkdir()
    monkeypatch.setattr(traditional_tools,'ARTIFACT_ROOT',root)
    path=root/'source.json';raw=b'{"observations":[]}'
    path.write_bytes(raw)
    return root,path,raw,hashlib.sha256(raw).hexdigest()


def test_valid_package_material_is_bounded_and_hash_checked(tmp_path,monkeypatch):
    root,path,raw,digest=fixture(tmp_path,monkeypatch)
    assert _read_trivy_artifact(str(path),digest)=={'observations':[]}
    assert _read_trivy_artifact(str(path),'0'*64) is None
    outside=tmp_path/'outside';outside.write_bytes(raw)
    assert _read_trivy_artifact(str(outside),digest) is None


@pytest.mark.parametrize('kind',['leaf_link','parent_link','hardlink','fifo','oversized','deep_json','bad_json'])
def test_package_reader_rejects_unsafe_or_unusable_material(tmp_path,monkeypatch,kind):
    root,path,raw,digest=fixture(tmp_path,monkeypatch)
    if kind=='leaf_link':
        link=root/'link';link.symlink_to(path);path=link
    elif kind=='parent_link':
        real=root/'real';real.mkdir();(real/'source').write_bytes(raw)
        link=root/'link';link.symlink_to(real,target_is_directory=True);path=link/'source'
    elif kind=='hardlink':
        link=root/'hard';os.link(path,link);path=link
    elif kind=='fifo':
        path=root/'pipe';os.mkfifo(path)
    else:
        raw=b' '*256001 if kind=='oversized' else b'['*2000+b'0'+b']'*2000 if kind=='deep_json' else b'not-json'
        path.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest()
    assert _read_trivy_artifact(str(path),digest) is None


@pytest.mark.parametrize('mode',['same_bytes_replacement','append'])
def test_package_reader_rejects_changed_file_even_when_original_hash_matches(tmp_path,monkeypatch,mode):
    root,path,raw,digest=fixture(tmp_path,monkeypatch)
    original=proof_materials.os.read;changed=False
    def read(fd,size):
        nonlocal changed
        value=original(fd,size)
        if not changed:
            changed=True
            if mode=='same_bytes_replacement':
                replacement=root/'replacement';replacement.write_bytes(raw);replacement.replace(path)
            else:
                with path.open('ab') as stream:stream.write(b' ')
        return value
    monkeypatch.setattr(proof_materials.os,'read',read)
    assert _read_trivy_artifact(str(path),digest) is None
