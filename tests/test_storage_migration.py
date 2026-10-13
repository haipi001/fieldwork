import os
from pathlib import Path
from unittest.mock import patch
import pytest
from storage_migration import preflight


def directories(tmp_path):
    root=tmp_path.resolve()
    source=root/'source';target=root/'target'
    source.mkdir();target.mkdir(mode=0o700)
    (source/'nested').mkdir();(source/'nested'/'file').write_bytes(b'fixture')
    return source,target


def test_inventory_and_no_mutation(tmp_path):
    source,target=directories(tmp_path)
    result=preflight(source,str(target))
    assert result['inventory']['regular_files']==1
    assert result['inventory']['logical_bytes']==7
    assert result['filesystem_prerequisites_ok']
    assert result['offline_required'] and not result['snapshot_consistent']
    assert not result['migration_available'] and not result['data_copied']
    assert (source/'nested'/'file').read_bytes()==b'fixture' and not list(target.iterdir())


@pytest.mark.parametrize('mode',['same','parent','child','relative','symlink','linked_parent'])
def test_unsafe_destination_rejected(tmp_path,mode):
    source,target=directories(tmp_path)
    if mode=='same':destination=source
    elif mode=='parent':destination=source.parent
    elif mode=='child':destination=source/'nested'
    elif mode=='relative':destination=Path('target')
    elif mode=='symlink':
        destination=source.parent/'link';destination.symlink_to(target,target_is_directory=True)
    else:
        link=source.parent/'link';link.symlink_to(target,target_is_directory=True)
        (target/'nested').mkdir();destination=link/'nested'
    with pytest.raises(ValueError):preflight(source,str(destination))


def test_special_files_and_destination_contents_block(tmp_path):
    source,target=directories(tmp_path)
    (source/'linked').symlink_to(source/'nested'/'file')
    os.mkfifo(source/'pipe')
    (target/'keep').write_text('keep')
    result=preflight(source,str(target))
    assert result['inventory']['unsupported_entries']==2
    assert set(result['blockers'])=={'unsupported_entries','destination_not_empty'}
    assert (target/'keep').read_text()=='keep'


def test_capacity_is_checked_on_destination(tmp_path):
    source,target=directories(tmp_path)
    capacity=type('Capacity',(),{'f_bavail':1,'f_frsize':4096})()
    with patch('storage_migration.os.fstatvfs',return_value=capacity):result=preflight(source,str(target))
    assert result['available_bytes']==4096 and result['required_bytes']==64*1024*1024+14
    assert result['blockers']==['insufficient_destination_space']


def test_entry_limit_fails_without_partial_success(tmp_path):
    source,target=directories(tmp_path)
    with patch('storage_migration.MAX_ENTRIES',1),pytest.raises(ValueError,match='directory_entry_limit'):
        preflight(source,str(target))


@pytest.mark.parametrize('mode', ['relative', 'symlink', 'linked_parent'])
def test_unsafe_source_rejected(tmp_path, mode):
    source, target = directories(tmp_path)
    if mode == 'relative':
        candidate = Path('source')
    elif mode == 'symlink':
        candidate = source.parent / 'source-link'
        candidate.symlink_to(source, target_is_directory=True)
    else:
        link = source.parent / 'parent-link'
        link.symlink_to(source, target_is_directory=True)
        candidate = link / 'nested'
    with pytest.raises(ValueError):
        preflight(candidate, target)
    assert not list(target.iterdir())


def test_same_size_rewrite_with_restored_mtime_blocks_preflight(tmp_path):
    source, target = directories(tmp_path)
    original_stat = os.stat
    original = original_stat(source / 'nested' / 'file')
    reads = 0
    def changed_stat(path, *args, **kwargs):
        nonlocal reads
        value = original_stat(path, *args, **kwargs)
        if path == 'file' and kwargs.get('dir_fd') is not None:
            reads += 1
            if reads == 2:
                # Metadata race: same inode, size and restored mtime, newer ctime.
                class Changed:
                    def __getattr__(self, name):
                        return getattr(value, name)
                    st_ctime_ns = original.st_ctime_ns + 1
                return Changed()
        return value
    with patch('storage_migration.os.stat', side_effect=changed_stat):
        result = preflight(source, target)
    assert result['inventory']['changed_entries'] == 1
    assert not result['filesystem_prerequisites_ok']
    assert not list(target.iterdir())


def test_public_destination_blocks_without_changing_permissions(tmp_path):
    source, target = directories(tmp_path)
    target.chmod(0o755)
    result = preflight(source, target)
    assert not result['destination_owner_private']
    assert 'destination_not_owner_private' in result['blockers']
    assert not result['filesystem_prerequisites_ok']
    assert target.stat().st_mode & 0o777 == 0o755
    assert not list(target.iterdir())
