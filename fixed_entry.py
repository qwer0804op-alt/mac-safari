#!/usr/bin/env python3
"""Optional fixed HTTPS entry point for Mac Remote (macOS and Windows).

A fixed site keeps the current device's public key and its current tunnel address. A browser
opens the fixed address, the site checks a fresh signature from this device and then redirects
to the current tunnel. Screens, clicks, codes and session cookies never pass through the site.

Usage: fixed_entry.py configure <https-origin> | clear | status | fingerprint [--runtime DIR]
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote, urlsplit

import certificates

QUICK_HOST = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.trycloudflare\.com')
PROOF_PATH = re.compile(r'/\.well-known/mac-remote-proof/([a-f0-9]{48})')
SITE_NAME = re.compile(r'(?:[a-z0-9-]+\.)+[a-z]{2,63}')
CONFIG_NAME = 'fixed-entry.txt'
INVALID_ORIGIN = '고정 사이트의 HTTPS 첫 화면 주소를 입력해 주세요.'


def default_runtime():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Mac Remote Pairing'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Mac Remote Pairing'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'mac-remote-pairing'


def normalize_origin(text):
    parsed = urlsplit(str(text).strip())
    try:
        name = parsed.hostname or ''
        port = parsed.port
    except ValueError:
        raise ValueError(INVALID_ORIGIN) from None
    if (parsed.scheme != 'https' or parsed.netloc.lower() != name or port is not None or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or
            not SITE_NAME.fullmatch(name) or name.endswith('.trycloudflare.com')):
        raise ValueError(INVALID_ORIGIN)
    return 'https://' + name


def save_origin(runtime, origin):
    origin = normalize_origin(origin)
    runtime = Path(runtime)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    (runtime / CONFIG_NAME).write_text(origin + '\n', encoding='ascii')
    return origin


def load_origin(runtime):
    path = Path(runtime) / CONFIG_NAME
    try:
        text = path.read_text(encoding='ascii').strip()
    except FileNotFoundError:
        return None
    return normalize_origin(text) if text else None


def clear_origin(runtime):
    (Path(runtime) / CONFIG_NAME).unlink(missing_ok=True)


class FixedEntry:
    def __init__(self, runtime, origin):
        self.runtime = Path(runtime)
        self.origin = normalize_origin(origin)
        self.key = self.runtime / 'private' / 'fixed-entry-key.pem'
        self.sign_slots = threading.BoundedSemaphore(2)
        self._public_key = None

    def ensure_key(self):
        self.key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.key.exists():
            return
        with tempfile.TemporaryDirectory(prefix='key-', dir=self.key.parent) as name:
            staged = Path(name) / 'key.pem'
            certificates.run('genrsa', '-out', staged, 2048)
            os.chmod(staged, 0o600)
            try:
                os.link(staged, self.key)
            except FileExistsError:
                pass
            except OSError:
                if not self.key.exists():
                    shutil.copyfile(staged, self.key)

    def public_key(self):
        if self._public_key is None:
            self.ensure_key()
            self._public_key = certificates.run('rsa', '-in', self.key, '-pubout', '-outform', 'DER')
        return self._public_key

    def fingerprint(self):
        return hashlib.sha256(self.public_key()).hexdigest()[:16]

    def sign(self, *fields):
        self.ensure_key()
        process = subprocess.run(
            [certificates.openssl_path(), 'dgst', '-sha256', '-sign', str(self.key)],
            input='\n'.join(fields).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, creationflags=certificates.NO_WINDOW)
        if process.returncode:
            raise RuntimeError('기기 서명에 실패했습니다.')
        return base64.b64encode(process.stdout).decode('ascii')

    def proof(self, host, nonce):
        return self.sign('proof', self.origin, host, nonce)

    def registration(self, host, now=None):
        stamp = int(time.time() if now is None else now)
        return {'host': host, 'ts': stamp, 'pub': base64.b64encode(self.public_key()).decode('ascii'),
                'signature': self.sign('register', self.origin, host, str(stamp))}

    def registration_url(self, host):
        # Only the public key, hostname and a short-lived signature enter the URL fragment.
        return self.origin + '/register#' + quote(json.dumps(self.registration(host)), safe='')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('configure', 'clear', 'status', 'fingerprint'))
    parser.add_argument('origin', nargs='?')
    parser.add_argument('--runtime', type=Path, default=default_runtime())
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            if not args.origin:
                parser.error('고정 사이트 주소가 필요합니다.')
            entry = FixedEntry(args.runtime, save_origin(args.runtime, args.origin))
            print('고정 주소:', entry.origin)
            print('기기 지문:', entry.fingerprint())
            print('처음 등록할 때 사이트에 위 16자리 기기 지문을 한 번 입력해 주세요.')
        elif args.command == 'clear':
            clear_origin(args.runtime)
            print('고정 주소 설정을 지웠습니다.')
        else:
            origin = load_origin(args.runtime)
            if origin is None:
                print('고정 주소가 설정되어 있지 않습니다.')
            elif args.command == 'status':
                print('고정 주소:', origin)
            else:
                print(FixedEntry(args.runtime, origin).fingerprint())
    except (ValueError, RuntimeError, OSError) as error:
        sys.exit(str(error))


if __name__ == '__main__':
    main()
