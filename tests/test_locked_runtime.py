import hashlib
from pathlib import Path
import pytest
from scripts import install_locked_runtime as runtime


def lock(tmp_path,monkeypatch,text):
    path=tmp_path/'runtime.lock';path.write_text(text);monkeypatch.setattr(runtime,'LOCK',path)
    monkeypatch.setattr(runtime.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(runtime.platform,'machine',lambda:'arm64')
    def forbidden(*args,**kwargs):raise AssertionError('Installer launched before validation')
    monkeypatch.setattr(runtime.subprocess,'run',forbidden)
    wheels=tmp_path/'wheels';wheels.mkdir();return wheels


def test_unreviewed_wheel_refused_before_install(tmp_path,monkeypatch):
    wheels=lock(tmp_path,monkeypatch,'foo==1.0 --hash=sha256:'+'a'*64+'\n')
    (wheels/'foo-1.0-py3-none-any.whl').write_bytes(b'changed fixture')
    with pytest.raises(ValueError,match='missing or changed'):runtime.install(wheels)


def test_lock_options_are_not_executable_configuration(tmp_path,monkeypatch):
    wheels=lock(tmp_path,monkeypatch,'--extra-index-url https://unknown.example.test\n')
    with pytest.raises(ValueError,match='Invalid runtime lock line'):runtime.install(wheels)


def test_duplicate_lock_package_refused(tmp_path,monkeypatch):
    entry='foo==1.0 --hash=sha256:'+'a'*64+'\n'
    wheels=lock(tmp_path,monkeypatch,entry+entry)
    with pytest.raises(ValueError,match='Duplicate'):runtime.install(wheels)


def test_linked_runtime_parent_refused(tmp_path,monkeypatch):
    payload=b'fixture';sha=hashlib.sha256(payload).hexdigest()
    wheels=lock(tmp_path,monkeypatch,'foo==1.0 --hash=sha256:'+sha+'\n')
    (wheels/'foo-1.0-py3-none-any.whl').write_bytes(payload)
    home=tmp_path.resolve()/'home';support=home/'Library/Application Support';support.mkdir(parents=True)
    other=tmp_path.resolve()/'other';other.mkdir()
    (support/'Fieldwork').symlink_to(other,target_is_directory=True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    with pytest.raises(ValueError):runtime.install(wheels)
