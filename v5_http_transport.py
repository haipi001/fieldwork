"""Supervisor for pinned read-only HTTP workers with loopback port sandbox grants."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


CHILD_SCRIPT = Path(__file__).with_name('v5_http_transport_child.py')


def request_once(spec, addresses, check_current=None):
    import traditional_runtime as http
    from reporting import redact
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise RuntimeError('isolated HTTP replay requires the macOS sandbox')
    if spec.method.upper() != 'GET' or spec.body is not None or not addresses:
        raise ValueError('isolated HTTP replay requires a pinned GET')
    parsed = urlsplit(spec.url)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    address = sorted(addresses)[0]
    # Seatbelt's remote-ip grammar supports localhost or *, not arbitrary IP literals.
    # Keep this first transport explicitly local until an external-target sandbox is available.
    if not ipaddress.ip_address(address).is_loopback:
        raise RuntimeError('isolated HTTP transport currently supports loopback targets only')
    endpoint = f'localhost:{port}'
    encoded = json.dumps({'url': spec.url, 'address': address, 'headers': spec.headers}).encode()
    if len(encoded) > 131072:
        raise ValueError('isolated HTTP input exceeds limit')
    def quote(value):
        return json.dumps(str(value))
    with tempfile.TemporaryDirectory(prefix='fieldwork-http-') as directory, tempfile.NamedTemporaryFile(dir=Path.home(), prefix='fieldwork-http-canary-') as canary:
        stage = Path(directory).resolve()
        script = stage / 'worker.py'
        shutil.copyfile(CHILD_SCRIPT, script)
        script_sha = hashlib.sha256(script.read_bytes()).hexdigest()
        executable, runtime = Path(sys.executable).resolve(), Path(sys.prefix).resolve()
        if runtime in (Path('/'), Path.home()):
            raise RuntimeError('Python runtime is too broad for sandboxing')
        profile = '\n'.join([
            '(version 1)', '(allow default)', '(deny process-fork)', '(deny network*)',
            f'(allow network-outbound (remote ip {quote(endpoint)}))',
            '(deny file-read* ' + ' '.join(f'(subpath {quote(p)})' for p in (Path.home(), Path('/Volumes'), Path('/private/tmp'), Path('/tmp'))) + ')',
            '(allow file-read* ' + ' '.join(f'(subpath {quote(p)})' for p in (runtime, executable.parent, stage)) + ')',
            '(deny file-write*)',
        ])
        process = subprocess.Popen(['/usr/bin/sandbox-exec', '-p', profile, str(executable), '-I', str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=directory,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(stage), 'TMPDIR': str(stage), 'PYTHONDONTWRITEBYTECODE': '1',
                 'FIELDWORK_DENIED_CANARY': canary.name})
        deadline = time.monotonic() + 12
        pending = encoded
        try:
            while True:
                if check_current:
                    check_current()
                if time.monotonic() >= deadline:
                    raise TimeoutError('isolated HTTP replay timed out')
                try:
                    output, _ = process.communicate(pending, timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            if process.returncode != 0 or len(output) > 100000:
                error_type = 'WorkerFailure'
                try:
                    candidate = json.loads(output).get('error_type', '')
                    if candidate in {'PermissionError', 'ValueError', 'OSError', 'TimeoutError', 'SSLCertVerificationError', 'ConnectionRefusedError'}:
                        error_type = candidate
                except (ValueError, AttributeError):
                    pass
                raise ValueError('isolated HTTP worker rejected request: ' + error_type)
            value = json.loads(output)
            if value.get('pid') != process.pid or value.get('ppid') != os.getpid() or value.get('file_read_denied') is not True:
                raise ValueError('isolated HTTP process attestation failed')
            if hashlib.sha256(script.read_bytes()).hexdigest() != script_sha:
                raise ValueError('isolated HTTP script changed')
            body = base64.b64decode(value['body'], validate=True)
            if len(body) > 65536 or not isinstance(value['status'], int) or not 100 <= value['status'] <= 599:
                raise ValueError('isolated HTTP output invalid')
            text = body.decode(errors='replace')
            # Raw output only lives in memory; artifacts receive the existing redacted representation.
            return {'status': value['status'], 'body_sha256': hashlib.sha256(body).hexdigest(), 'body_bytes': len(body),
                    'headers': {k: redact(v) for k, v in value['headers'].items()}, 'body_preview': http._safe_body_preview(text),
                    '_transient_body': text, 'process_execution': {
                        'parent_pid': os.getpid(), 'child_pid': process.pid, 'exit_code': process.returncode,
                        'sandbox': 'macos-seatbelt', 'sandbox_profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
                        'script_sha256': script_sha, 'file_read_denied': True, 'pinned_address': address,
                        'observed_by': 'fieldwork_local_supervisor', 'scope': 'transport_only',
                        'network_grant': 'loopback_exact_port'}}
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
