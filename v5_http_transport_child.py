"""One authorized, pinned GET. Standalone stdlib worker; no application imports."""
import base64
import http.client
import hashlib
import ipaddress
import json
import os
import resource
import socket
import ssl
import sys
from urllib.parse import urlsplit


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        with open(os.environ['FIELDWORK_DENIED_CANARY'], 'rb') as probe:
            probe.read(1)
    except PermissionError:
        pass
    else:
        raise ValueError('sandbox probe failed')
    encoded = sys.stdin.buffer.read(131073)
    if len(encoded) > 131072:
        raise ValueError('input too large')
    payload = json.loads(encoded)
    parsed = urlsplit(payload['url'])
    address = str(ipaddress.ip_address(payload['address']))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('unsupported URL')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    headers = payload['headers']
    if not isinstance(headers, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or len(k) > 120 or len(v) > 16384
        or any(c in k + v for c in '\r\n')
        or k.lower() in {'host', 'connection', 'content-length', 'transfer-encoding', 'proxy-authorization', 'proxy-connection'}
        for k, v in headers.items()
    ):
        raise ValueError('unsupported headers')
    if parsed.scheme == 'https':
        connection = http.client.HTTPSConnection(parsed.hostname, port, timeout=8, context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=8)
    # HTTPSConnection still verifies the original hostname and uses it for SNI.
    connection._create_connection = lambda _endpoint, timeout, source_address=None: socket.create_connection((address, port), timeout, source_address)
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError('response exceeds limit')
        fields = payload.get('scalar_fields', [])
        if not isinstance(fields, list) or len(fields) > 2 or any(not isinstance(p, str) or len(p) > 160 for p in fields):
            raise ValueError('invalid scalar fields')
        scalars = {}
        for path in fields:
            try:
                value = json.loads(body)
                for part in path.split('.'):
                    value = value[part]
                scalars[path] = hashlib.sha256(json.dumps(str(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest() if type(value) in (str, int) and str(value) else None
            except (ValueError, TypeError, KeyError):
                scalars[path] = None
        result = {'pid': os.getpid(), 'ppid': os.getppid(), 'file_read_denied': True,
                  'status': response.status, 'body': base64.b64encode(body).decode('ascii'),
                  'scalar_sha256': scalars,
                  'headers': {k: v for k, v in response.getheaders() if k.lower() in {'content-type', 'location', 'etag'}}}
        print(json.dumps(result), flush=True)
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Credentials, response bodies and URLs never enter diagnostics.
        print(json.dumps({'error_type': type(error).__name__}), flush=True)
        sys.exit(1)
