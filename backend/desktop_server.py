"""Desktop-owned uvicorn server; stdin EOF ends the owned backend."""
import argparse
import os
import stat
import threading


def run_owned_server(application, port, *, fd=None):
    import uvicorn
    parent = int(os.environ.get('FIELDWORK_DESKTOP_PARENT_PID', '0'))
    if parent <= 1 or parent != os.getppid() or not stat.S_ISFIFO(os.fstat(0).st_mode):
        raise RuntimeError('desktop server requires its parent lifetime pipe')
    if not 1 <= port <= 65535:
        raise ValueError('invalid desktop port')
    server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port,
                                         fd=fd, access_log=False, timeout_graceful_shutdown=3))
    def watch_parent():
        try:
            os.read(0, 1)  # The desktop never writes bytes, only holds the writer.
        finally:
            server.should_exit = True
            fallback = threading.Timer(5, lambda: os._exit(125))
            fallback.daemon = True
            fallback.start()
    threading.Thread(target=watch_parent, daemon=True, name='fieldwork-desktop-lifetime').start()
    server.run()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    arguments = parser.parse_args()
    run_owned_server('app:app', arguments.port)
