#!/usr/bin/env python3
"""Generate an Ed25519 collector key and sign a Fieldwork telemetry import."""
import argparse
import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def generate(args):
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path = Path(args.private_key).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(private_bytes)
    path.chmod(0o600)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(json.dumps({'key_id': args.key_id, 'public_key': base64.b64encode(public).decode()}, indent=2))


def sign(args):
    content = Path(args.content).read_text()
    signed_at = args.signed_at or datetime.now(timezone.utc).isoformat()
    nonce = args.nonce or secrets.token_hex(16)
    payload = {'schema': 'fieldwork-agent-telemetry-signature/1', 'audit_id': args.audit_id,
               'collector_id': args.collector_id, 'sequence': args.sequence, 'nonce': nonce,
               'signed_at': signed_at, 'kind': args.kind, 'provenance': 'operator_telemetry',
               'source_name': args.source_name, 'input_sha256': hashlib.sha256(content.encode()).hexdigest()}
    private = serialization.load_pem_private_key(Path(args.private_key).expanduser().read_bytes(), password=None)
    envelope = {'kind': args.kind, 'content': content, 'provenance': 'operator_telemetry',
                'source_name': args.source_name, 'independent_attested': False,
                'self_report_complete': False, 'collector_id': args.collector_id,
                'sequence': args.sequence, 'nonce': nonce, 'signed_at': signed_at,
                'signature': base64.b64encode(private.sign(canonical(payload))).decode()}
    print(json.dumps(envelope, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    create = commands.add_parser('generate-key')
    create.add_argument('--key-id', required=True)
    create.add_argument('--private-key', required=True)
    create.set_defaults(run=generate)
    signer = commands.add_parser('sign')
    signer.add_argument('--private-key', required=True)
    signer.add_argument('--audit-id', required=True)
    signer.add_argument('--collector-id', required=True)
    signer.add_argument('--sequence', required=True, type=int)
    signer.add_argument('--content', required=True)
    signer.add_argument('--kind', default='generic_json')
    signer.add_argument('--source-name', default='signed local collector')
    signer.add_argument('--nonce')
    signer.add_argument('--signed-at')
    signer.set_defaults(run=sign)
    args = parser.parse_args()
    args.run(args)


if __name__ == '__main__':
    main()
