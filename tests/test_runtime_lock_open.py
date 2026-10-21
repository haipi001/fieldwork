from pathlib import Path
import os
import pytest
import storage_access as storage


def test_runtime_lock_retries_verified_directory_without_absolute_fallback(tmp_path,monkeypatch):
    original=os.open;attempts=[]
    def open_file(path,flags,*args,**kwargs):
        if path=='.storage-access.lock':
            attempts.append((flags,kwargs.get('dir_fd')))
            if len(attempts)==1:raise FileNotFoundError('injected transient openat failure')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',open_file)
    with storage.storage_access(tmp_path):
        assert len(attempts)==2
        assert attempts[0][1]==attempts[1][1] and attempts[0][1] is not None
        assert all(flags&os.O_NOFOLLOW for flags,_ in attempts)
    assert (tmp_path/'.storage-access.lock').stat().st_mode&0o777==0o600


def test_runtime_lock_does_not_follow_replaced_directory_on_retry(tmp_path,monkeypatch):
    original=os.open;directory=tmp_path/'runtime';directory.mkdir();moved=tmp_path/'original'
    attempts=[]
    def open_file(path,flags,*args,**kwargs):
        if path=='.storage-access.lock':
            attempts.append(path);directory.rename(moved);directory.mkdir()
            raise FileNotFoundError('injected directory replacement')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',open_file)
    with pytest.raises(RuntimeError,match='storage_directory_changed'):
        with storage.storage_access(directory):pytest.fail('must not acquire replaced directory')
    assert len(attempts)==1
    assert not (directory/'.storage-access.lock').exists() and not (moved/'.storage-access.lock').exists()


def test_runtime_lock_retries_are_bounded_and_symlink_is_rejected(tmp_path,monkeypatch):
    original=os.open;attempts=[]
    def open_file(path,flags,*args,**kwargs):
        if path=='.storage-access.lock':attempts.append(path);raise FileNotFoundError('persistent failure')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',open_file)
    with pytest.raises(FileNotFoundError):
        with storage.storage_access(tmp_path):pytest.fail('must not acquire missing lock')
    assert len(attempts)==3
    monkeypatch.setattr(os,'open',original)
    target=tmp_path/'owned';target.write_text('must remain unchanged')
    (tmp_path/'.storage-access.lock').symlink_to(target)
    with pytest.raises(OSError):
        with storage.storage_access(tmp_path):pytest.fail('must reject symlink lock')
    assert target.read_text()=='must remain unchanged'
