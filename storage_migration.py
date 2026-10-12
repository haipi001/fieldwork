"""Read-only prerequisites for a future offline data-directory migration."""
import os
import stat
from pathlib import Path

MAX_ENTRIES = 100000


def _plain_directory(path):
    """Reject symbolic links anywhere in an absolute directory path."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute_directory_required')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('directory_or_parent_not_plain')
    return path


def _open_directory(path):
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def preflight(source, destination):
    source = _plain_directory(Path(source).resolve())
    destination = _plain_directory(destination)
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('overlapping_data_directories')
    source_info, destination_info = source.stat(), destination.stat()
    if (source_info.st_dev, source_info.st_ino) == (destination_info.st_dev, destination_info.st_ino):
        raise ValueError('same_data_directory')
    descriptor = _open_directory(source)
    totals = {'regular_files': 0, 'directories': 0, 'logical_bytes': 0,
              'unsupported_entries': 0, 'unreadable_entries': 0, 'changed_entries': 0}
    entries = 0

    def scan(directory, depth=0):
        nonlocal entries
        if depth > 64:
            raise ValueError('directory_depth_limit')
        directory_before = os.fstat(directory)
        for name in os.listdir(directory):
            entries += 1
            if entries > MAX_ENTRIES:
                raise ValueError('directory_entry_limit')
            try:
                before = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(before.st_mode):
                    totals['regular_files'] += 1
                    totals['logical_bytes'] += before.st_size
                    file_descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    try:
                        opened = os.fstat(file_descriptor)
                        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                            totals['changed_entries'] += 1
                    finally:
                        os.close(file_descriptor)
                elif stat.S_ISDIR(before.st_mode):
                    totals['directories'] += 1
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                            totals['changed_entries'] += 1
                        else:
                            scan(child, depth + 1)
                    finally:
                        os.close(child)
                else:
                    totals['unsupported_entries'] += 1
                after = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    totals['changed_entries'] += 1
            except OSError:
                totals['unreadable_entries'] += 1
        if os.fstat(directory).st_mtime_ns != directory_before.st_mtime_ns:
            totals['changed_entries'] += 1
    try:
        scan(descriptor)
    finally:
        os.close(descriptor)
    target = _open_directory(destination)
    try:
        empty = not os.listdir(target)
        capacity = os.fstatvfs(target)
        free = capacity.f_bavail * capacity.f_frsize
    finally:
        os.close(target)
    # A staged copy and rollback copy plus a reserve; actual copying must recheck.
    required = totals['logical_bytes'] * 2 + 64 * 1024 * 1024
    blockers = []
    if not empty: blockers.append('destination_not_empty')
    if not os.access(destination, os.W_OK | os.X_OK): blockers.append('destination_not_writable')
    if free < required: blockers.append('insufficient_destination_space')
    for key in ('unsupported_entries', 'unreadable_entries', 'changed_entries'):
        if totals[key]: blockers.append(key)
    return {'source_directory': str(source), 'destination_directory': str(destination),
            'inventory': totals, 'destination_empty': empty, 'available_bytes': free,
            'required_bytes': required, 'filesystem_prerequisites_ok': not blockers,
            'blockers': blockers, 'offline_required': True, 'snapshot_consistent': False,
            'migration_available': False, 'data_copied': False, 'configuration_changed': False,
            'excludes': ['keychain', 'application_bundle'],
            'next_steps': ['complete_backup', 'stop_all_writers', 'recheck_inventory',
                           'copy_and_verify_all_files', 'atomic_switch', 'startup_acceptance', 'rollback_acceptance']}
