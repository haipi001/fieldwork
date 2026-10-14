"""Verified migration staging; never switches configuration or deletes source data.

Cooperative locks and repeated reads detect changes, not arbitrary external
writers. A staged copy is not approved for activation until offline acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from storage_access import storage_access
from storage_migration import _open_directory, _plain_directory
from storage_paths import validate_directory

MAX_ENTRIES = 100000
MAX_BYTES = 2 * 1024 ** 3
LOCK = '.storage-access.lock'


def _fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def _walk(source, destination=None):
    records = []
    total = 0

    def visit(directory, output, prefix='', depth=0):
        nonlocal total
        if depth > 64:
            raise ValueError('directory_depth_limit')
        before_directory = _fingerprint(os.fstat(directory))
        for name in sorted(os.listdir(directory)):
            if not prefix and name == LOCK:
                continue
            if len(records) >= MAX_ENTRIES:
                raise ValueError('directory_entry_limit')
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            relative = prefix + name
            if stat.S_ISDIR(before.st_mode):
                records.append({'path': relative, 'type': 'directory', 'identity': _fingerprint(before)})
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                target = None
                try:
                    if _fingerprint(os.fstat(child)) != _fingerprint(before):
                        raise ValueError('source_changed')
                    if output is not None:
                        os.mkdir(name, 0o700, dir_fd=output)
                        target = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output)
                    visit(child, target, relative + '/', depth + 1)
                finally:
                    os.close(child)
                    if target is not None:
                        os.close(target)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                total += before.st_size
                if total > MAX_BYTES:
                    raise ValueError('data_size_limit')
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                target = None
                try:
                    if _fingerprint(os.fstat(descriptor)) != _fingerprint(before):
                        raise ValueError('source_changed')
                    if output is not None:
                        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600 | (before.st_mode & 0o100), dir_fd=output)
                    digest = hashlib.sha256()
                    count = 0
                    while data := os.read(descriptor, 1024 * 1024):
                        count += len(data)
                        if count > before.st_size:
                            raise ValueError('source_changed')
                        digest.update(data)
                        if target is not None:
                            view = memoryview(data)
                            while view:
                                written = os.write(target, view)
                                if written <= 0:
                                    raise OSError('short_write')
                                view = view[written:]
                    if count != before.st_size or _fingerprint(os.fstat(descriptor)) != _fingerprint(before):
                        raise ValueError('source_changed')
                    if target is not None:
                        os.fsync(target)
                    records.append({'path': relative, 'type': 'file', 'size': count,
                                    'sha256': digest.hexdigest(), 'identity': _fingerprint(before)})
                finally:
                    os.close(descriptor)
                    if target is not None:
                        os.close(target)
            else:
                raise ValueError('unsupported_source_entry')
            if _fingerprint(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _fingerprint(before):
                raise ValueError('source_changed')
        if _fingerprint(os.fstat(directory)) != before_directory:
            raise ValueError('source_changed')
        if output is not None:
            os.fsync(output)
    visit(source, destination)
    return records


def _content(records):
    return [{k: v for k, v in item.items() if k != 'identity'} for item in records]


def _write_json(directory, name, value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2)
    if len(payload.encode('utf-8')) > 16 * 1024 ** 2:
        raise ValueError('stage_manifest_size_limit')
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(directory)


def stage(source, destination):
    source = _plain_directory(source)
    destination = validate_directory(str(destination))
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('overlapping_data_directories')
    with storage_access(source, maintenance=True), storage_access(destination, maintenance=True):
        source_fd, target_fd = _open_directory(source), None
        try:
            target_fd = _open_directory(destination)
            if set(os.listdir(target_fd)) - {LOCK}:
                raise ValueError('destination_not_empty')
            baseline = _walk(source_fd)
            size = sum(item.get('size', 0) for item in baseline)
            capacity = os.fstatvfs(target_fd)
            if capacity.f_bavail * capacity.f_frsize < size * 2 + 64 * 1024 ** 2:
                raise ValueError('insufficient_destination_space')
            name = 'migration-stage-' + secrets.token_hex(12)
            os.mkdir(name, 0o700, dir_fd=target_fd)
            stage_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target_fd)
            try:
                os.mkdir('data', 0o700, dir_fd=stage_fd)
                data_fd = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
                try:
                    copied = _walk(source_fd, data_fd)
                    verified = _walk(data_fd)
                finally:
                    os.close(data_fd)
                if baseline != copied or baseline != _walk(source_fd):
                    raise ValueError('source_changed')
                if _content(copied) != _content(verified):
                    raise ValueError('destination_verification_failed')
                from storage_relocation import relocate_staged_database
                relocation = relocate_staged_database(source, destination / name / 'data')
                data_fd = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
                try:
                    final_files = _walk(data_fd)
                finally:
                    os.close(data_fd)
                if baseline != _walk(source_fd):
                    raise ValueError('source_changed')
                result = {'format': 'fieldwork-migration-stage-v1', 'state': 'files_verified_not_activated',
                          'files': _content(final_files), 'logical_bytes': sum(item.get('size', 0) for item in final_files),
                          'reference_relocation': relocation,
                          'configuration_changed': False, 'activation_allowed': False,
                          'all_writers_stopped_verified': False,
                          'excluded': [LOCK], 'metadata_scope': 'file_bytes_and_owner_execute_bit'}
                _write_json(stage_fd, 'manifest.json', result)
                os.fsync(target_fd)
                return {'stage_directory': str(destination / name), **result}
            except BaseException as error:
                _write_json(stage_fd, 'FAILED.json', {'state': 'failed_do_not_activate', 'error_type': type(error).__name__})
                raise
            finally:
                os.close(stage_fd)
        finally:
            os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)


def verify_stage(directory):
    """Recheck saved bytes before further maintenance; never grants activation."""
    directory = validate_directory(str(directory))
    root = _open_directory(directory)
    try:
        if 'FAILED.json' in os.listdir(root):
            raise ValueError('failed_stage')
        descriptor = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 ** 2:
                raise ValueError('invalid_stage_manifest')
            raw = stream.read(16 * 1024 ** 2 + 1)
        if len(raw) > 16 * 1024 ** 2:
            raise ValueError('invalid_stage_manifest')
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or manifest.get('format') != 'fieldwork-migration-stage-v1'
                or manifest.get('state') != 'files_verified_not_activated'
                or manifest.get('activation_allowed') is not False
                or not isinstance(manifest.get('files'), list)):
            raise ValueError('invalid_stage_manifest')
        data = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            actual = _content(_walk(data))
        finally:
            os.close(data)
        if actual != manifest['files']:
            raise ValueError('staged_files_changed')
        return {'verified': True, 'file_entries': len(actual),
                'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'activation_allowed': False}
    finally:
        os.close(root)
