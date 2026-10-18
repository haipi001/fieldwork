"""Ephemeral task-local TLS identity; never modifies system certificate trust."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import os
import ssl
import tempfile
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


@contextmanager
def broker_authority():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Fieldwork task-local broker')])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(hours=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(key, hashes.SHA256()))
    cache = {}
    with tempfile.TemporaryDirectory(prefix='fieldwork-broker-tls-') as directory:
        def context(host):
            if host in cache:
                return cache[host]
            leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            try:
                san = x509.IPAddress(ipaddress.ip_address(host))
            except ValueError:
                san = x509.DNSName(host.encode('idna').decode())
            leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Fieldwork scoped HTTPS endpoint')])
            leaf = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(name).public_key(leaf_key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                    .not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([san]), critical=False)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
                    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
                    .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                    .sign(key, hashes.SHA256()))
            cert = Path(directory) / f'leaf-{len(cache)}.pem'
            private = Path(directory) / f'key-{len(cache)}.pem'
            cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
            private.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            os.chmod(private, 0o600)
            result = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            result.minimum_version = ssl.TLSVersion.TLSv1_2
            result.load_cert_chain(cert, private)
            cache[host] = result
            return result
        yield ca.public_bytes(serialization.Encoding.PEM), context
