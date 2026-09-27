import base64
import json
import pytest
import native_ai_binding as binding


def setup_registry(tmp_path,monkeypatch):
    path=tmp_path/'registry.json'
    value={'schema':'fieldwork-native-binding/1','team_id':'ABCDEFGHIJ',
        'public_key':base64.b64encode(b'k'*32).decode()}
    path.write_text(json.dumps(value))
    monkeypatch.setattr(binding,'REGISTRY',path)
    def identity(path):
        info=path.stat();return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns
    monkeypatch.setattr(binding,'protected_file',identity)
    monkeypatch.setattr(binding,'verify_installed_collector',lambda team:True)
    return path,value


def test_matching_key_needs_protected_registry_and_verified_binary(tmp_path,monkeypatch):
    path,value=setup_registry(tmp_path,monkeypatch)
    assert binding.authorized({'public_key':value['public_key']})
    assert not binding.authorized({'public_key':base64.b64encode(b'x'*32).decode()})
    def rejected(*args):raise binding.NativeLaunchError('fixture signature failure')
    monkeypatch.setattr(binding,'verify_installed_collector',rejected)
    assert not binding.authorized({'public_key':value['public_key']})


def test_unprotected_or_modified_registry_never_authorizes(tmp_path,monkeypatch):
    path,value=setup_registry(tmp_path,monkeypatch)
    def changed(*args):path.write_bytes(path.read_bytes()+b' ')
    monkeypatch.setattr(binding,'verify_installed_collector',changed)
    assert not binding.authorized({'public_key':value['public_key']})
    def rejected(*args):raise binding.NativeLaunchError('fixture permissions')
    monkeypatch.setattr(binding,'protected_file',rejected)
    assert not binding.authorized({'public_key':value['public_key']})


@pytest.mark.parametrize('data',[{'schema':'wrong'},[],{'schema':'fieldwork-native-binding/1',
    'team_id':'ABCDEFGHIJ','public_key':'not-base64','extra':'private'}])
def test_bad_registry_is_not_trusted(data,tmp_path,monkeypatch):
    path,value=setup_registry(tmp_path,monkeypatch);path.write_text(json.dumps(data))
    assert not binding.authorized({'public_key':value['public_key']})
