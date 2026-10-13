"""Relocate supported file references only in a verified staging database.

Historical JSON, receipts and evidence bytes are never rewritten. This function
is not a complete migration or an authorization to activate the staging copy.
"""
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

from storage_migration import _open_directory, _plain_directory


def _verified_file(root, parts):
    descriptor = _open_directory(root)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = following
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('relocation_target_not_regular')
            digest = hashlib.sha256()
            while chunk := os.read(file_fd, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(file_fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('relocation_target_changed')
            return digest.hexdigest()
        finally:
            os.close(file_fd)
    finally:
        os.close(descriptor)


def relocate_staged_database(source_root, staged_root):
    source_root = _plain_directory(source_root)
    staged_root = _plain_directory(staged_root)
    if (source_root == staged_root or source_root.is_relative_to(staged_root)
            or staged_root.is_relative_to(source_root)):
        raise ValueError('overlapping_data_directories')
    database = staged_root / 'src_control.db'
    if not database.exists():
        return {'database_present': False, 'changed_references': 0}
    if database.is_symlink() or not database.is_file() or database.stat().st_nlink != 1:
        raise ValueError('invalid_staged_database')
    counts = {}
    # Caller holds both maintenance locks and owns the private staging directory.
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('staged_database_integrity_failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column, digest_column in [('artifacts', 'uri', 'sha256'),
                                              ('submission_packages_v2', 'export_path', None)]:
            if table not in tables:
                continue
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchone():
                raise ValueError('staged_reference_triggers_unsupported')
            counts[table] = 0
            columns = 'id,' + column + (',' + digest_column if digest_column else '')
            for row in db.execute(f'SELECT {columns} FROM {table}').fetchall():
                identifier, raw = row[:2]
                if raw is None and digest_column is None:
                    continue
                if not isinstance(raw, str) or not raw or '\x00' in raw:
                    raise ValueError('invalid_stored_file_reference')
                path = Path(raw)
                if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(source_root):
                    raise ValueError('stored_file_reference_outside_source')
                parts = path.relative_to(source_root).parts
                if not parts:
                    raise ValueError('invalid_stored_file_reference')
                digest = _verified_file(staged_root, parts)
                if digest_column and digest != row[2]:
                    raise ValueError('relocated_artifact_digest_mismatch')
                updated = db.execute(f'UPDATE {table} SET {column}=? WHERE id=? AND {column}=?',
                           (str(staged_root.joinpath(*parts)), identifier, raw))
                if updated.rowcount != 1:
                    raise ValueError('staged_reference_not_unique')
                counts[table] += 1
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('staged_database_integrity_failed')
    return {'database_present': True, 'changed_references': sum(counts.values()), 'tables': counts,
            'historical_json_rewritten': False, 'activation_allowed': False}
