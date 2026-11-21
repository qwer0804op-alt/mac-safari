import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import certificates  # noqa: E402
import fixed_entry  # noqa: E402

HOST = 'cloud-test-fixture.trycloudflare.com'
ORIGIN = 'https://entry.example'


def load_server():
    spec = spec_from_file_location('mac_remote_server_fixed', ROOT / 'server.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OriginTest(unittest.TestCase):
    def test_only_plain_https_site_addresses_are_accepted(self):
        self.assertEqual(fixed_entry.normalize_origin(' https://Entry.Example/ '), ORIGIN)
        for value in ('http://entry.example', 'https://entry.example/path', 'https://entry.example:8443', 'https://user@entry.example',
                      'https://entry.example/?a=1', 'https://entry.example/#x', 'https://localhost', 'https://127.0.0.1',
                      'https://abc.trycloudflare.com', 'entry.example', ''):
            with self.assertRaises(ValueError, msg=value):
                fixed_entry.normalize_origin(value)

    def test_saved_address_round_trips_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(fixed_entry.load_origin(folder))
            fixed_entry.save_origin(folder, 'https://entry.example/')
            self.assertEqual(fixed_entry.load_origin(folder), ORIGIN)
            fixed_entry.clear_origin(folder)
            self.assertIsNone(fixed_entry.load_origin(folder))


class DeviceKeyTest(unittest.TestCase):
    def verify(self, entry, signature_b64, *fields):
        import base64
        with tempfile.TemporaryDirectory() as folder:
            public = Path(folder) / 'public.pem'
            public.write_bytes(certificates.run('rsa', '-in', entry.key, '-pubout'))
            signature = Path(folder) / 'signature.bin'
            signature.write_bytes(base64.b64decode(signature_b64))
            message = Path(folder) / 'message.txt'
            message.write_bytes('\n'.join(fields).encode())
            result = subprocess.run([certificates.openssl_path(), 'dgst', '-sha256', '-verify', str(public), '-signature', str(signature), str(message)],
                                    capture_output=True, text=True)
            return result.returncode == 0

    def test_key_is_created_once_and_signatures_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            entry = fixed_entry.FixedEntry(folder, ORIGIN)
            entry.ensure_key()
            first = entry.public_key()
            self.assertEqual(len(entry.fingerprint()), 16)
            if os.name != 'nt':
                self.assertEqual(entry.key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(fixed_entry.FixedEntry(folder, ORIGIN).public_key(), first)
            registration = entry.registration(HOST, now=1234)
            self.assertEqual(set(registration), {'host', 'ts', 'pub', 'signature'})
            self.assertTrue(self.verify(entry, registration['signature'], 'register', ORIGIN, HOST, '1234'))
            self.assertFalse(self.verify(entry, registration['signature'], 'register', 'https://other.example', HOST, '1234'))
            self.assertTrue(self.verify(entry, entry.proof(HOST, 'a' * 48), 'proof', ORIGIN, HOST, 'a' * 48))
            url = entry.registration_url(HOST)
            self.assertTrue(url.startswith(ORIGIN + '/register#'))
            self.assertNotIn('BEGIN', url)


class ProofEndpointTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.server_module = load_server()
        store = certificates.CertificateStore(root / 'certs')
        self.context = store.prepare([])
        self.ca = store.ca_pem
        self.entry = fixed_entry.FixedEntry(root / 'entry', ORIGIN)
        self.entry.ensure_key()
        state = self.server_module.RemoteState(lambda event: None, internet=True)
        self.server = self.server_module.RemoteHTTPServer(('127.0.0.1', 0), self.context, state, fixed_entry=self.entry)
        self.server.public_host = HOST
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, host=HOST):
        client = ssl.create_default_context(cafile=str(self.ca))
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=10, context=client)
        try:
            connection.request('GET', path, headers={'Host': host})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_proof_is_signed_only_for_the_public_host(self):
        nonce = 'b' * 48
        status, body = self.get('/.well-known/mac-remote-proof/' + nonce)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['signature'])
        self.assertEqual(self.get('/.well-known/mac-remote-proof/' + nonce, host='evil.example')[0], 403)
        self.assertEqual(self.get('/.well-known/mac-remote-proof/not-a-nonce')[0], 404)

    @unittest.skipUnless(shutil.which('node'), 'requires Node.js')
    def test_fixed_site_registers_and_redirects_to_this_device(self):
        config = {
            'origin': ORIGIN, 'host': HOST, 'port': self.port, 'ca': Path(self.ca).read_text(),
            'registration': self.entry.registration(HOST), 'fingerprint': self.entry.fingerprint(),
        }
        path = Path(self.folder.name) / 'cross.json'
        path.write_text(json.dumps(config))
        result = subprocess.run(['node', str(ROOT / 'tests' / 'fixed_entry_cross.mjs'), str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'ok'), result.stderr)


class LauncherEventsTest(unittest.TestCase):
    def test_windows_console_shows_the_registration_address_and_fingerprint(self):
        import windows_app
        console = windows_app.Console(internet=True)
        lines = console.handle({'event': 'register', 'url': ORIGIN + '/register#x', 'fingerprint': '0123456789abcdef'})
        self.assertIn(ORIGIN + '/register#x', lines)
        self.assertTrue(any('0123456789abcdef' in line for line in lines))
        self.assertTrue(console.handle({'event': 'register-failed'}))


if __name__ == '__main__':
    unittest.main()
