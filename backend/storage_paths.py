"""One process-stable storage location shared by backend data writers."""
import os
import stat
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_KEY = 'FIELDWORK_DATA_DIRECTORY'


def validate_directory(value):
    if not isinstance(value,str) or not value or any(ord(c)<32 for c in value):
        raise ValueError('invalid_data_directory')
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute_data_directory_required')
    descriptor=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            following=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            os.close(descriptor);descriptor=following
        info=os.fstat(descriptor)
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise ValueError('data_directory_must_be_owner_private')
    except OSError as error:
        raise ValueError('data_directory_unavailable_or_linked') from error
    finally:
        os.close(descriptor)
    return path


@lru_cache(maxsize=1)
def data_directory():
    value=os.environ.get(ENVIRONMENT_KEY)
    if value is None:
        return PROJECT_ROOT/'data'
    # Bad explicit configuration must stop startup, never open a different DB.
    return validate_directory(value)
