#!/usr/bin/env python3
"""Create a private local CA and a host-validated HTTPS certificate for Mac Remote.

The CA is kept stable across IP changes; only the leaf certificate is reissued.
Private material is stored in a user-only directory in Application Support.
"""
import fcntl
import hashlib
import ipaddress
import os
from pathlib import Path
import plistlib
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
from uuid import NAMESPACE_URL, uuid5

OPENSSL = '/usr/bin/openssl'


def run(*args):
    completed = subprocess.run([OPENSSL, *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        detail = completed.stderr.decode('utf-8', 'replace').strip()[-1000:]
        raise RuntimeError('인증서 처리에 실패했어. ' + detail)
    return completed.stdout


def local_addresses():
    result = set()
    for _, interface in socket.if_nameindex():
        if not interface.startswith('en'):
            continue
        proc = subprocess.run(['/usr/sbin/ipconfig', 'getifaddr', interface], capture_output=True, text=True, check=False)
        try:
            address = ipaddress.IPv4Address(proc.stdout.strip())
            if not address.is_unspecified and not address.is_loopback:
                result.add(str(address))
        except ValueError:
            pass
    return sorted(result, key=lambda value: int(ipaddress.ip_address(value)))


def atomic_install(src, dst, mode):
    os.chmod(src, mode)
    os.replace(src, dst)


def matches_key(cert, key):
    # A modulus check avoids ever printing or returning private-key material.
    cert_modulus = run('x509', '-in', cert, '-noout', '-modulus').strip()
    key_modulus = run('rsa', '-in', key, '-noout', '-modulus').strip()
    return cert_modulus == key_modulus


class CertificateStore:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.private = self.runtime / 'private'
        self.ca_key = self.private / 'mac-remote-ca-key.pem'
        self.ca_pem = self.private / 'mac-remote-ca.pem'
        self.public_cert = self.runtime / 'Mac-Remote-인증서.cer'
        self.safari_profile = self.runtime / 'Mac-Remote-Safari.mobileconfig'
        self.server_key = self.private / 'mac-remote-server-key.pem'
        self.server_cert = self.private / 'mac-remote-server.pem'

    def export_safari_profile(self):
        # The public DER is the only data from the certificate store in the profile.
        certificate = self.public_cert.read_bytes()
        fingerprint = hashlib.sha256(certificate).hexdigest()
        profile_id = f'local.macremote.pairing.{fingerprint}'
        payload_id = f'{profile_id}.root'
        contents = plistlib.dumps({
            'PayloadType': 'Configuration',
            'PayloadVersion': 1,
            'PayloadIdentifier': profile_id,
            'PayloadUUID': str(uuid5(NAMESPACE_URL, f'urn:{profile_id}')),
            'PayloadDisplayName': 'Mac 리모컨 Safari 인증서',
            'PayloadDescription': 'Safari에서 Mac 리모컨에 HTTPS로 연결할 때 쓰는 인증서를 설치해.',
            'PayloadRemovalDisallowed': False,
            'PayloadContent': [{
                'PayloadType': 'com.apple.security.root',
                'PayloadVersion': 1,
                'PayloadIdentifier': payload_id,
                'PayloadUUID': str(uuid5(NAMESPACE_URL, f'urn:{payload_id}')),
                'PayloadDisplayName': 'Mac Remote Pairing CA',
                'PayloadCertificateFileName': self.public_cert.name,
                'PayloadContent': certificate,
            }],
        })
        if not self.safari_profile.is_file() or self.safari_profile.read_bytes() != contents:
            with tempfile.TemporaryDirectory(prefix='profile-', dir=self.runtime) as name:
                staged = Path(name) / self.safari_profile.name
                staged.write_bytes(contents)
                atomic_install(staged, self.safari_profile, 0o644)
        return self.safari_profile

    def create_ca(self, tmp):
        config = tmp / 'ca.conf'
        config.write_text('''[req]
distinguished_name = dn
prompt = no
x509_extensions = ca_ext
[dn]
CN = Mac Remote Pairing CA
[ca_ext]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
''', encoding='ascii')
        key, pem = tmp / 'ca-key.pem', tmp / 'ca.pem'
        run('req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650', '-config', config, '-extensions', 'ca_ext', '-keyout', key, '-out', pem)
        if not matches_key(pem, key):
            raise RuntimeError('CA 키가 인증서와 일치하지 않아.')
        atomic_install(key, self.ca_key, 0o600)
        atomic_install(pem, self.ca_pem, 0o600)

    def ensure_ca(self, tmp):
        if not any(path.exists() for path in (self.ca_key, self.ca_pem, self.public_cert)):
            self.create_ca(tmp)
        if not self.ca_key.is_file() or not self.ca_pem.is_file():
            raise RuntimeError('기존 CA 인증서나 개인키를 찾을 수 없어.')
        os.chmod(self.ca_key, 0o600)
        if not matches_key(self.ca_pem, self.ca_key):
            raise RuntimeError('기존 CA 인증서와 개인키가 일치하지 않아.')
        # Preserve the CA identity that phones have already trusted.
        try:
            run('x509', '-in', self.ca_pem, '-checkend', str(366 * 86400), '-noout')
        except RuntimeError as exc:
            raise RuntimeError('로컬 CA가 1년 이내 만료돼. 인증서를 갱신해야 해.') from exc
        expected = run('x509', '-in', self.ca_pem, '-outform', 'DER')
        if not self.public_cert.is_file() or self.public_cert.read_bytes() != expected:
            ca_public = tmp / 'ca.cer'
            ca_public.write_bytes(expected)
            atomic_install(ca_public, self.public_cert, 0o644)

    def create_server(self, tmp, ips):
        alt_names = '\n'.join(f'IP.{index} = {ip}' for index, ip in enumerate(ips, 1))
        config = tmp / 'server.conf'
        config.write_text('''[server_ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName = @alt_names
[alt_names]
DNS.1 = localhost
''' + alt_names + '\n', encoding='ascii')
        key, csr, cert = tmp / 'server-key.pem', tmp / 'server.csr', tmp / 'server.pem'
        run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-subj', '/CN=Mac Remote', '-keyout', key, '-out', csr)
        serial = '0x' + format(secrets.randbits(159) | 1, 'x')
        run('x509', '-req', '-in', csr, '-CA', self.ca_pem, '-CAkey', self.ca_key, '-set_serial', serial, '-days', '364', '-sha256', '-extfile', config, '-extensions', 'server_ext', '-out', cert)
        context = self.validate(cert, key, ips)
        atomic_install(key, self.server_key, 0o600)
        atomic_install(cert, self.server_cert, 0o600)
        return context

    def validate(self, cert, key, ips):
        run('verify', '-CAfile', self.ca_pem, '-purpose', 'sslserver', cert)
        decoded = ssl._ssl._test_decode_cert(str(cert))
        actual = set(decoded.get('subjectAltName', ()))
        expected = {('DNS', 'localhost')} | {('IP Address', ip) for ip in ips}
        if actual != expected:
            raise RuntimeError('인증서의 접속 주소가 현재 주소와 달라.')
        run('x509', '-in', cert, '-checkend', str(7 * 86400), '-noout')
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(cert, key)
        client_context = ssl.create_default_context(cafile=str(self.ca_pem))
        for name in ['localhost', *ips]:
            verify_handshake(server_context, client_context, name)
        return server_context

    def prepare(self, ips):
        self.private.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self.runtime, self.private):
            directory.chmod(0o700)
        lock_path = self.private / 'certificates.lock'
        with lock_path.open('a') as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            with tempfile.TemporaryDirectory(prefix='cert-', dir=self.private) as name:
                tmp = Path(name)
                self.ensure_ca(tmp)
                certificate_ips = ['127.0.0.1', *ips]
                try:
                    self.server_key.chmod(0o600)
                    return self.validate(self.server_cert, self.server_key, certificate_ips)
                except (RuntimeError, OSError, ValueError):
                    return self.create_server(tmp, certificate_ips)


def verify_handshake(server_context, client_context, name):
    server_sock, client_sock = socket.socketpair()
    server_sock.settimeout(5)
    client_sock.settimeout(5)
    server_errors = []

    def serve():
        try:
            with server_context.wrap_socket(server_sock, server_side=True):
                pass
        except OSError as exc:
            server_errors.append(exc)

    with server_sock, client_sock:
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            with client_context.wrap_socket(client_sock, server_hostname=name):
                pass
        finally:
            thread.join(timeout=6)
        if thread.is_alive() or server_errors:
            raise RuntimeError('인증서 TLS 자체 검사에 실패했어.')
