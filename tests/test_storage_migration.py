import os
from pathlib import Path
from unittest.mock import patch
import pytest
from storage_migration import preflight


def directories(tmp_path):
    root=tmp_path.resolve()
    source=root/'source';target=root/'target'
    source.mkdir();target.mkdir()
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
