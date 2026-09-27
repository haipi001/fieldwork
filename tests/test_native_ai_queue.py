import os
import pytest
from native_ai_queue import EncryptedPendingQueue


def queue(path,key=b'k'*32,**kwargs):
    return EncryptedPendingQueue(path,key,audit_id=kwargs.get('audit_id','audit'),collector_id='collector')


def test_encrypted_queue_survives_restart_without_plaintext_and_never_overwrites(tmp_path):
    path=tmp_path/'queue'; body=b'{"private_fixture":"secret fixture path"}'
    with queue(path) as first:
        first.save(body);first.save(body)
        with pytest.raises(ValueError,match='不能覆盖'):first.save(b'next')
    assert b'secret fixture path' not in (path/'pending.enc').read_bytes()
    assert (path/'pending.enc').stat().st_mode&0o777==0o600
    with queue(path) as restarted:
        assert restarted.load()==body
        with pytest.raises(ValueError,match='不一致'):restarted.acknowledge(b'other')
        restarted.acknowledge(body)
        assert restarted.load() is None


def test_wrong_key_or_scope_and_tampering_are_not_silently_discarded(tmp_path):
    path=tmp_path/'queue'
    with queue(path) as original:original.save(b'fixture')
    for kwargs in [{'key':b'x'*32},{'audit_id':'other'}]:
        with queue(path,**kwargs) as reader:
            with pytest.raises(ValueError,match='校验失败'):reader.load()
    data=bytearray((path/'pending.enc').read_bytes());data[-1]^=1;(path/'pending.enc').write_bytes(data)
    with queue(path) as reader:
        with pytest.raises(ValueError,match='校验失败'):reader.load()
    assert (path/'pending.enc').exists()


def test_symlinks_and_permissive_directories_are_rejected(tmp_path):
    path=tmp_path/'wide';path.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='0700'):queue(path)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):queue(link)
    secure=tmp_path/'secure'
    with queue(secure) as reader:
        (secure/'pending.enc').symlink_to(tmp_path/'outside')
        with pytest.raises(OSError):reader.load()


def test_failed_publication_cleans_temporary_file(tmp_path,monkeypatch):
    path=tmp_path/'queue'
    def fail(*args,**kwargs):raise OSError('fixture disk failure')
    with queue(path) as reader:
        monkeypatch.setattr(os,'link',fail)
        with pytest.raises(OSError):reader.save(b'fixture')
        assert reader.load() is None
    assert list(path.iterdir())==[]


def test_nonregular_queue_file_does_not_block_reader(tmp_path):
    path=tmp_path/'queue'
    with queue(path) as reader:
        os.mkfifo(path/'pending.enc',0o600)
        with pytest.raises(ValueError,match='类型无效'):reader.load()
