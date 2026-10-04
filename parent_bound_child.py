"""Bind a standalone worker lifetime to an inherited, read-only pipe."""
import os


_GUARD = '''
import os as _lifetime_os
import stat as _lifetime_stat
import threading as _lifetime_threading

def _install_parent_lifetime_guard():
    try:
        descriptor = int(_lifetime_os.environ['FIELDWORK_PARENT_LIFETIME_FD'])
        expected_parent = int(_lifetime_os.environ['FIELDWORK_PARENT_PID'])
        if descriptor < 3 or expected_parent != _lifetime_os.getppid():
            raise ValueError('invalid lifetime binding')
        if not _lifetime_stat.S_ISFIFO(_lifetime_os.fstat(descriptor).st_mode):
            raise ValueError('invalid lifetime pipe')
    except Exception:
        _lifetime_os._exit(125)
    def watch():
        try:
            # The supervisor never writes bytes. EOF means its sole writer closed.
            _lifetime_os.read(descriptor, 1)
        finally:
            _lifetime_os._exit(125)
    _lifetime_threading.Thread(target=watch, daemon=True,
                              name='fieldwork-parent-lifetime').start()

_install_parent_lifetime_guard()
'''


def stage_child(source, destination):
    # Compile separately so worker __future__ imports retain their legal position.
    destination.write_text(_GUARD + '\nexec(compile(' + repr(source.read_bytes()) +
                           ", __file__, 'exec'))\n")


def spawn_child(launch, *args, **kwargs):
    reader, writer = os.pipe()
    try:
        environment = dict(kwargs.pop('env'))
        environment.update(FIELDWORK_PARENT_LIFETIME_FD=str(reader), FIELDWORK_PARENT_PID=str(os.getpid()))
        descriptors = tuple(kwargs.pop('pass_fds', ())) + (reader,)
        process = launch(*args, env=environment, pass_fds=descriptors, **kwargs)
    except BaseException:
        os.close(writer)
        raise
    finally:
        os.close(reader)
    process._fieldwork_lifetime_writer = writer
    return process


def close_lifetime(process):
    descriptor = getattr(process, '_fieldwork_lifetime_writer', None)
    if descriptor is not None:
        process._fieldwork_lifetime_writer = None
        os.close(descriptor)
