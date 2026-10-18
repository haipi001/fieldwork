"""Bounded, value-redacted inventory of old-root references in a staged database."""
import json
from pathlib import Path
import sqlite3

MAX_CELLS = 1000000
MAX_VALUE_BYTES = 2 * 1024 ** 2


def _matcher(old_root):
    root = str(Path(old_root))
    if not Path(root).is_absolute() or '..' in Path(root).parts:
        raise ValueError('absolute_source_required')
    root = root.rstrip('/') + '/'
    def contains(value):
        if isinstance(value, str):
            if root in value or value == root[:-1]:
                return True
            # Decode JSON strings, including escaped non-ASCII directory names.
            try:
                decoded = json.loads(value, object_pairs_hook=list)
            except RecursionError as error:
                raise ValueError('reference_json_depth_limit') from error
            except ValueError:
                return False
            if isinstance(decoded, str):
                return root in decoded or decoded == root[:-1]
            def walk(item, depth=0):
                if depth > 64:
                    raise ValueError('reference_json_depth_limit')
                if isinstance(item, str): return root in item or item == root[:-1]
                if isinstance(item, (list, tuple)): return any(walk(x, depth+1) for x in item)
                if isinstance(item, dict): return any(walk(k,depth+1) or walk(v,depth+1) for k,v in item.items())
                return False
            return walk(decoded)
        return False
    return contains


def audit_database(database, old_root):
    contains = _matcher(old_root)
    counts = []
    cells = 0
    with sqlite3.connect(Path(database).as_uri()+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        tables = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        for table, sql in tables:
            if 'VIRTUAL TABLE' in (sql or '').upper():
                raise ValueError('virtual_table_reference_audit_unsupported')
            quoted = '"'+table.replace('"','""')+'"'
            cursor = db.execute(f'SELECT * FROM {quoted}')
            columns = [column[0] for column in cursor.description]
            found = [0]*len(columns)
            for row in cursor:
                cells += len(row)
                if cells > MAX_CELLS: raise ValueError('reference_cell_limit')
                for index, value in enumerate(row):
                    if isinstance(value, (str,bytes)) and len(value if isinstance(value,bytes) else value.encode()) > MAX_VALUE_BYTES:
                        raise ValueError('reference_value_limit')
                    if contains(value): found[index] += 1
            counts.extend({'table':table,'column':columns[i],'matching_rows':count} for i,count in enumerate(found) if count)
    return {'complete': True, 'scope': 'sqlite_text_and_decoded_json_old_root_references',
            'unresolved_reference_cells':sum(x['matching_rows'] for x in counts), 'locations':counts,
            'values_included':False, 'external_files_audited':False, 'activation_allowed':False}


def audit_json_files(directory, records, old_root):
    """Check manifest-bound JSON bytes without following links or exposing values."""
    import hashlib
    import os
    import stat
    from storage_migration import _open_directory, _plain_directory
    directory = _plain_directory(directory)
    contains = _matcher(old_root)
    locations = []
    checked = 0
    for record in records:
        if record.get('type') != 'file' or not record.get('path', '').lower().endswith('.json'):
            continue
        relative = Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('invalid_reference_file_path')
        descriptor = _open_directory(directory)
        try:
            for part in relative.parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = following
            file_fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(file_fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_VALUE_BYTES:
                    raise ValueError('reference_file_limit_or_type')
                raw = stream.read(MAX_VALUE_BYTES + 1)
            if len(raw) > MAX_VALUE_BYTES or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
                raise ValueError('reference_file_changed')
            text = raw.decode('utf-8')
            try:
                json.loads(text)
            except (ValueError, RecursionError) as error:
                raise ValueError('invalid_reference_json_file') from error
            checked += 1
            if contains(text):
                locations.append({'relative_path': str(relative), 'old_root_reference': True})
        finally:
            os.close(descriptor)
    return {'complete': True, 'scope': 'manifest_listed_utf8_json_files', 'checked_files': checked,
            'matching_files': len(locations), 'locations': locations, 'values_included': False,
            'activation_allowed': False}
