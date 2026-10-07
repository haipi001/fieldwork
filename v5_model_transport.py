"""Bounded trusted local-model HTTP process; this is not an untrusted-tool sandbox."""
from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Callable

from parent_bound_child import close_lifetime, spawn_child, stage_child

CHILD = Path(__file__).with_name('v5_model_transport_child.py')


class ModelTransportError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__('model transport rejected request: ' + code)


def request_model(url: str, payload: dict, timeout_ms: int,
                  check_current: Callable[[], None] | None = None, *,
                  location: str = "local", api_key: str | None = None) -> bytes:
    encoded = json.dumps({'url': url, 'payload': payload, 'timeout_ms': timeout_ms, 'location': location, 'api_key': api_key},
                         ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 64_000 or not 1 <= timeout_ms <= 45_000:
        raise ValueError('model request exceeds transport budget')
    deadline = time.monotonic() + timeout_ms / 1000
    if check_current:
        check_current()
    with tempfile.TemporaryDirectory(prefix='fieldwork-model-') as directory:
        script = Path(directory) / 'worker.py'
        stage_child(CHILD, script)
        arguments = [sys.executable, '-I', str(script)]
        if location == 'cloud':
            # The isolated interpreter cannot import application dependencies.
            # Stage the installed CA bundle as trusted transport configuration,
            # never as a caller-supplied path or an endpoint exception.
            import certifi
            bundle = Path(certifi.where()).read_bytes()
            if not bundle or len(bundle) > 1_000_000:
                raise ValueError('invalid installed CA bundle')
            trust = Path(directory) / 'ca.pem'
            trust.write_bytes(bundle)
            trust.chmod(0o400)
            arguments.append(str(trust))
        process = spawn_child(subprocess.Popen, arguments,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              cwd=directory, env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
        pending = encoded
        try:
            while True:
                if check_current:
                    check_current()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('local model wall-clock budget exceeded')
                try:
                    output, _ = process.communicate(pending, timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            if check_current:
                check_current()
            if time.monotonic() > deadline:
                raise TimeoutError('local model wall-clock budget exceeded')
            if process.returncode != 0:
                if output == b'redirect_forbidden':
                    raise ValueError('local model redirects are forbidden')
                code = output.decode('ascii', errors='replace')
                allowed = {'tls_verification_failed', 'dns_resolution_failed', 'network_timeout',
                           'cloud_address_forbidden', 'output_limit', 'endpoint_forbidden',
                           'cloud_requires_https', 'invalid_credential', 'local_credentials_not_supported',
                           'invalid_timeout', 'input_limit', 'transport_error'}
                if code not in allowed and not re.fullmatch(r'http_status_[1-5][0-9]{2}', code):
                    code = 'transport_error'
                raise ModelTransportError(code)
            if len(output) > 256_000:
                raise ValueError('local model output exceeds 256 KB')
            return output
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                process.communicate()
            finally:
                close_lifetime(process)
