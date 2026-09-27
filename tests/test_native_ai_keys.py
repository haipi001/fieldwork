import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import native_ai_keys as keys


@pytest.fixture
def store(tmp_path,monkeypatch):
    # Exercise persistence as the test user; production root/path gates tested separately.
    tmp_path.chmod(0o700)
    monkeypatch.setattr(keys,'_root_required',lambda:None)
    monkeypatch.setattr(keys,'_directory_fd',lambda:os.open(tmp_path,os.O_RDONLY|os.O_DIRECTORY))
    return tmp_path


def test_restart_retains_signing_and_encryption_keys(store):
    first=keys.load_or_create();second=keys.load_or_create()
    assert first.public_key_base64()==second.public_key_base64()
    second.signing_key.public_key().verify(first.signing_key.sign(b'fixture'),b'fixture')
    nonce=b'n'*12
    encrypted=AESGCM(first.queue_key).encrypt(nonce,b'pending',b'context')
    assert AESGCM(second.queue_key).decrypt(nonce,encrypted,b'context')==b'pending'
    assert (store/'keys.bin').stat().st_mode&0o777==0o600
    assert 'queue_key' not in repr(first) and first.public_key_base64() not in repr(first)
    assert list(store.iterdir())==[store/'keys.bin']


@pytest.mark.parametrize('content',[b'',b'x'*63,b'x'*65])
def test_corrupt_keys_are_never_replaced(store,content):
    path=store/'keys.bin';path.write_bytes(content);path.chmod(0o600)
    with pytest.raises(ValueError):keys.load_or_create()
    assert path.read_bytes()==content


def test_concurrent_initialization_preserves_one_identity(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        initialized=list(pool.map(lambda _:keys.load_or_create(),range(8)))
    assert len({item.public_key_base64() for item in initialized})==1
    assert len({item.queue_key for item in initialized})==1
    assert list(store.iterdir())==[store/'keys.bin']


def test_unprotected_and_linked_key_files_rejected(store):
    keys.load_or_create();path=store/'keys.bin';path.chmod(0o644)
    with pytest.raises(PermissionError):keys.load_or_create()
    path.chmod(0o600);os.link(path,store/'alias')
    with pytest.raises(PermissionError):keys.load_or_create()
    (store/'alias').unlink();path.rename(store/'original');path.symlink_to(store/'original')
    with pytest.raises(OSError):keys.load_or_create()


def test_normal_user_cannot_initialize_keys(monkeypatch):
    monkeypatch.setattr(keys.os,'geteuid',lambda:501)
    with pytest.raises(PermissionError):keys._root_required()


def test_unprotected_directory_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(keys,'KEY_DIRECTORY',tmp_path)
    tmp_path.chmod(0o777)
    with pytest.raises(PermissionError):keys._directory_fd()
