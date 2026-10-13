import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import storage_staging as staging
from storage_access import storage_access


def directories(tmp_path):
    source = tmp_path.resolve() / 'source'; source.mkdir(mode=0o700)
    destination = tmp_path.resolve() / 'destination'; destination.mkdir(mode=0o700)
    (source / 'nested').mkdir()
    (source / 'nested' / 'evidence.bin').write_bytes(b'\x00\xff evidence')
    (source / 'empty').mkdir()
    return source, destination


def test_copy_verifies_bytes_and_retains_source(tmp_path):
    source, destination = directories(tmp_path)
    result = staging.stage(source, destination)
    root = Path(result['stage_directory'])
    assert (root / 'data/nested/evidence.bin').read_bytes() == b'\x00\xff evidence'
    assert (root / 'data/empty').is_dir()
    assert not (root / 'data/.storage-access.lock').exists()
    assert (source / 'nested/evidence.bin').read_bytes() == b'\x00\xff evidence'
    assert not result['activation_allowed'] and not result['configuration_changed']
    assert not result['all_writers_stopped_verified']
    assert json.loads((root / 'manifest.json').read_text())['files'] == result['files']
    assert (root / 'data/nested/evidence.bin').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('side', ['source', 'destination'])
def test_live_service_prevents_staging(tmp_path, side):
    source, destination = directories(tmp_path)
    with storage_access(source if side == 'source' else destination):
        with pytest.raises(RuntimeError, match='storage_in_use'):
            staging.stage(source, destination)
    assert not list(destination.glob('migration-stage-*'))


@pytest.mark.parametrize('kind', ['link', 'fifo', 'hardlink'])
def test_unsupported_source_never_enters_copy(tmp_path, kind):
    source, destination = directories(tmp_path)
    if kind == 'link': (source / 'bad').symlink_to(source / 'nested')
    elif kind == 'fifo': os.mkfifo(source / 'bad')
    else: os.link(source / 'nested/evidence.bin', source / 'bad')
    with pytest.raises(ValueError, match='unsupported_source_entry'):
        staging.stage(source, destination)
    assert not list(destination.glob('migration-stage-*'))


def test_change_between_inventory_and_copy_marks_failed_stage(tmp_path):
    source, destination = directories(tmp_path)
    walk = staging._walk
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (source / 'nested/evidence.bin').write_bytes(b'changed')
        return walk(*args, **kwargs)
    with patch.object(staging, '_walk', side_effect=changed), pytest.raises(ValueError, match='source_changed'):
        staging.stage(source, destination)
    root = next(destination.glob('migration-stage-*'))
    assert (root / 'FAILED.json').is_file()
    assert not (root / 'manifest.json').exists()


def test_corrupt_copy_fails_independent_readback(tmp_path):
    source, destination = directories(tmp_path)
    walk = staging._walk
    def corrupted(source_fd, destination_fd=None):
        result = walk(source_fd, destination_fd)
        if destination_fd is not None:
            root = next(destination.glob('migration-stage-*'))
            (root / 'data/nested/evidence.bin').write_bytes(b'corrupt')
        return result
    with patch.object(staging, '_walk', side_effect=corrupted), pytest.raises(ValueError, match='destination_verification_failed'):
        staging.stage(source, destination)
    root = next(destination.glob('migration-stage-*'))
    assert (root / 'FAILED.json').exists() and not (root / 'manifest.json').exists()


def test_nonempty_target_preserved(tmp_path):
    source, destination = directories(tmp_path)
    (destination / 'keep').write_text('keep')
    with pytest.raises(ValueError, match='destination_not_empty'):
        staging.stage(source, destination)
    assert (destination / 'keep').read_text() == 'keep'


def test_limit_prevents_partial_success(tmp_path):
    source, destination = directories(tmp_path)
    with patch.object(staging, 'MAX_BYTES', 1), pytest.raises(ValueError, match='data_size_limit'):
        staging.stage(source, destination)
    assert not list(destination.glob('migration-stage-*'))


@pytest.mark.parametrize('change', ['modify', 'add', 'delete', 'failed_marker'])
def test_saved_stage_reverification_rejects_changes(tmp_path, change):
    source, destination = directories(tmp_path)
    result = staging.stage(source, destination)
    root = Path(result['stage_directory'])
    assert staging.verify_stage(root)['verified']
    if change == 'modify': (root/'data/nested/evidence.bin').write_bytes(b'changed')
    elif change == 'add': (root/'data/additional').write_text('new')
    elif change == 'delete': (root/'data/nested/evidence.bin').unlink()
    else: (root/'FAILED.json').write_text('{}')
    with pytest.raises(ValueError, match='staged_files_changed|failed_stage'):
        staging.verify_stage(root)
