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


def test_installation_status_never_claims_live_monitoring(tmp_path,monkeypatch):
    path,value=setup_registry(tmp_path,monkeypatch)
    binary=tmp_path/'collector';binary.write_bytes(b'fixture')
    monkeypatch.setattr(binding,'INSTALL_PATH',binary)
    monkeypatch.setattr(binding.platform,'system',lambda:'Darwin')
    assert binding.installation_status()=={'status':'installed_not_connected','live_verified':False}
    assert value['public_key'] not in json.dumps(binding.installation_status())
    def rejected(*args):raise binding.NativeLaunchError('fixture')
    monkeypatch.setattr(binding,'verify_installed_collector',rejected)
    assert binding.installation_status()['status']=='installation_check_failed'
    binary.unlink()
    assert binding.installation_status()['status']=='not_installed'
    monkeypatch.setattr(binding.platform,'system',lambda:'Linux')
    assert binding.installation_status()['status']=='unsupported_platform'


def test_installation_check_rejects_registry_changes(tmp_path,monkeypatch):
    path,value=setup_registry(tmp_path,monkeypatch)
    monkeypatch.setattr(binding,'INSTALL_PATH',path)
    monkeypatch.setattr(binding.platform,'system',lambda:'Darwin')
    def changed(*args):path.write_bytes(path.read_bytes()+b' ')
    monkeypatch.setattr(binding,'verify_installed_collector',changed)
    assert binding.installation_status()=={'status':'installation_check_failed','live_verified':False}
