"""Read bounded imported source bytes against their saved material manifest."""
import hashlib
import json
import os
import stat
from pathlib import Path

from storage_migration import _open_directory, _plain_directory
from web3_contract_import import MAX_BYTES, MAX_FILES, safe_sources


def verified_sources(root, fingerprint):
    root = _plain_directory(root)
    descriptor = _open_directory(root)
    def read(parts, limit):
        parent = os.dup(descriptor)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent);parent = child
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(file_fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise ValueError('source_material_file_invalid')
                raw = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if len(raw) > limit or (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('source_material_changed')
                return raw
        finally:
            os.close(parent)
    try:
        manifest = json.loads(read(('source-manifest.json',), 2 * 1024 ** 2))
        standard = json.loads(read(('standard-input.json',), MAX_BYTES * 6 + 1024 ** 2))
        sources = safe_sources(standard.get('sources'))
        files = manifest.get('files')
        if manifest.get('fingerprint') != fingerprint or not isinstance(files, dict) or set(files) != set(sources) or len(files) > MAX_FILES:
            raise ValueError('source_manifest_mismatch')
        for name, entry in sources.items():
            raw = entry['content'].encode()
            recorded = files[name]
            if (not isinstance(recorded, dict) or recorded.get('bytes') != len(raw)
                    or recorded.get('sha256') != hashlib.sha256(raw).hexdigest()
                    or read(('sources', *Path(name).parts), MAX_BYTES) != raw):
                raise ValueError('source_material_digest_mismatch')
        return sources
    finally:
        os.close(descriptor)
