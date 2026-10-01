import json
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
import native_ai_registration as registration
import native_ai_binding as binding


@pytest.fixture
def installation(tmp_path,monkeypatch):
    private=tmp_path/'keys';private.mkdir(mode=0o700)
    registry=tmp_path/'collector-public.json'
    # Isolate root-only production path/signature gates; exercise actual key and file operations.
    monkeypatch.setattr(registration.native_ai_keys,'_root_required',lambda:None)
    monkeypatch.setattr(registration.native_ai_keys,'_directory_fd',lambda:os.open(private,os.O_RDONLY|os.O_DIRECTORY))
    monkeypatch.setattr(registration,'REGISTRY',registry)
    monkeypatch.setattr(registration,'_directory_fd',lambda:os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY))
    monkeypatch.setattr(registration,'verify_installed_collector',lambda team:'fixture-identity')
    monkeypatch.setattr(registration,'protected_binary',lambda path:'fixture-identity')
    return registry


def test_registration_is_public_only_and_idempotent(installation):
    first=registration.register_installation('ABCDEFGHIJ')
    info=installation.stat()
    second=registration.register_installation('ABCDEFGHIJ')
    assert first==second==json.loads(installation.read_bytes())
    assert set(first)=={'schema','team_id','public_key'}
    assert installation.stat().st_ino==info.st_ino
    assert info.st_mode&0o777==0o644
    assert 'queue_key' not in installation.read_text() and 'signing_key' not in installation.read_text()


def test_published_identity_matches_backend_binding(installation,monkeypatch):
    public=registration.register_installation('ABCDEFGHIJ')
    monkeypatch.setattr(binding,'REGISTRY',installation)
    def protected(path):
        info=path.stat();return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns
    monkeypatch.setattr(binding,'protected_file',protected)
    monkeypatch.setattr(binding,'verify_installed_collector',lambda team:True)
    assert binding.authorized({'public_key':public['public_key']})


def test_conflicting_or_corrupt_registry_is_never_overwritten(installation):
    registration.register_installation('ABCDEFGHIJ')
    original=installation.read_bytes()
    with pytest.raises(ValueError):registration.register_installation('OTHERTEAM1')
    assert installation.read_bytes()==original
    installation.write_bytes(b'broken')
    with pytest.raises(ValueError):registration.register_installation('ABCDEFGHIJ')
    assert installation.read_bytes()==b'broken'


def test_symlink_and_unsafe_permissions_are_rejected(installation):
    registration.register_installation('ABCDEFGHIJ');installation.chmod(0o666)
    with pytest.raises(PermissionError):registration.register_installation('ABCDEFGHIJ')
    installation.chmod(0o644);original=installation.with_name('original')
    installation.rename(original);installation.symlink_to(original)
    with pytest.raises(OSError):registration.register_installation('ABCDEFGHIJ')


def test_signature_failure_or_binary_change_does_not_publish(installation,monkeypatch):
    def failure(team):raise ValueError('fixture signature rejected')
    monkeypatch.setattr(registration,'verify_installed_collector',failure)
    with pytest.raises(ValueError):registration.register_installation('ABCDEFGHIJ')
    assert not installation.exists()
    assert not (installation.parent/'keys'/'keys.bin').exists()
    monkeypatch.setattr(registration,'verify_installed_collector',lambda team:'before')
    with pytest.raises(ValueError):registration.register_installation('ABCDEFGHIJ')
    assert not installation.exists()


def test_concurrent_registration_keeps_single_identity(installation):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:registration.register_installation('ABCDEFGHIJ'),range(8)))
    assert all(value==results[0] for value in results)
    assert sorted(path.name for path in installation.parent.iterdir())==['collector-public.json','keys']
