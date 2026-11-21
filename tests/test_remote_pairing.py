import http.client
import json
import queue
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_server():
    spec = spec_from_file_location('mac_remote_pairing', ROOT / 'server.py')
    module = module_from_spec(spec)
    with patch.object(sys, 'path', [str(ROOT), *sys.path]):
        spec.loader.exec_module(module)
    return module


class PairingTest(unittest.TestCase):
    def test_code_keeps_leading_zeros_and_expires_in_five_minutes(self):
        module = load_server()
        with patch.object(module, 'time') as clock, patch.object(module, 'secrets') as random:
            clock.monotonic.return_value = 100
            random.randbelow.return_value = 27
            events = []
            state = module.RemoteState(events.append)
            state.issue_code()
            self.assertEqual(events, [{'event': 'code', 'code': '0027'}])
            clock.monotonic.return_value = 399
            self.assertEqual(state.pair('0028')[0], 401)
            clock.monotonic.return_value = 400
            self.assertEqual(state.pair('0027')[0], 410)

    def test_internet_pairing_waits_for_matching_mac_decision(self):
        module = load_server()
        events = queue.Queue()
        state = module.RemoteState(events.put, internet=True)
        for allow in (False, True):
            state.issue_code()
            code = events.get()['code']
            with ThreadPoolExecutor(max_workers=1) as workers:
                response = workers.submit(state.pair, code)
                approval = events.get(timeout=2)
                self.assertEqual(approval['event'], 'approval')
                state.resolve_approval('unrelated', True)
                self.assertFalse(response.done())
                state.resolve_approval(approval['id'], allow)
                status, _, headers = response.result(timeout=2)
                self.assertEqual(status, 200 if allow else 403)
                self.assertEqual(bool(headers), allow)
                self.assertEqual(state.pair(code)[0], 410)
                events.get(timeout=2)

    def test_pairing_https_control_and_launcher_lifecycle(self):
        with tempfile.TemporaryDirectory() as name:
            self.exercise_launcher(Path(name).resolve(), self.exercise_pairing)

    def test_app_capture_requires_authenticated_request_and_matching_launcher_reply(self):
        with tempfile.TemporaryDirectory() as name:
            self.exercise_launcher(Path(name).resolve(), self.exercise_app_capture, '--app-capture')

    def test_capture_timeout_keeps_slot_until_late_reply(self):
        module = load_server()
        events = []
        bridge = module.AppCapture(events.append)
        self.addCleanup(bridge.stop)
        with patch.object(module, 'CAPTURE_SECONDS', 0):
            with self.assertRaisesRegex(RuntimeError, module.CAPTURE_TIMEOUT):
                bridge.read()
            with self.assertRaisesRegex(RuntimeError, module.CAPTURE_TIMEOUT):
                bridge.read()
        self.assertEqual(len(events), 1)
        capture = events[0]
        self.assertFalse(Path(capture['path']).parent.exists())
        bridge.resolve({'capture': capture['id'], 'error': None})
        self.assertIsNone(bridge.pending)

    def test_input_timeout_does_not_retry_or_dispatch_another_input(self):
        module = load_server()
        events = []
        bridge = module.AppInput(events.append)
        self.addCleanup(bridge.stop)
        with patch.object(module, 'INPUT_SECONDS', 0):
            with self.assertRaisesRegex(RuntimeError, module.INPUT_TIMEOUT):
                bridge.perform(['click', '1', '2'])
            with self.assertRaisesRegex(RuntimeError, '처리 중'):
                bridge.perform(['key', 'enter'])
        self.assertEqual(len(events), 1)
        bridge.resolve({'input': events[0]['id'], 'error': None})
        self.assertIsNone(bridge.pending)

    def exercise_launcher(self, root, exercise, *options):
        for name in ('server.py', 'certificates.py', 'internet_tunnel.py'):
            shutil.copyfile(ROOT / name, root / name)
        shutil.copytree(ROOT / 'public', root / 'public')
        helper = root / 'native' / 'remote-helper'
        helper.parent.mkdir()
        helper.write_text('#!/bin/sh\nif [ "$1" = screenshot ]; then printf "jpeg-test" > "$2"; fi\n')
        helper.chmod(0o700)
        runtime = root / 'runtime'
        events = queue.Queue()

        def read_events(output):
            for line in output:
                events.put(json.loads(line))

        with subprocess.Popen(
            [sys.executable, '-B', str(root / 'server.py'), '--host', '127.0.0.1', '--port', '0', '--runtime', str(runtime), *options],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as process:
            reader = threading.Thread(target=read_events, args=(process.stdout,), daemon=True)
            reader.start()
            try:
                ready = events.get(timeout=15)
                self.prepare_https_client(ready, runtime)
                exercise(process, events, ready['code'])
            finally:
                process.stdin.close()
                try:
                    status = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    raise
                reader.join(timeout=5)
            self.assertEqual(status, 0, process.stderr.read())
            with self.assertRaises(OSError):
                self.request('GET', '/api/status')

    def prepare_https_client(self, ready, runtime):
        self.assertEqual(ready['event'], 'ready')
        self.assertEqual(ready['urls'], [])
        self.port = ready['port']
        code = ready['code']
        self.assertTrue(re.fullmatch(r'[0-9]{4}', code))
        public_cert = Path(ready['certificate'])
        self.assertEqual(public_cert, runtime / 'Mac-Remote-인증서.cer')
        self.assertEqual((runtime / 'private' / 'mac-remote-ca-key.pem').stat().st_mode & 0o777, 0o600)
        self.context = ssl.create_default_context(cadata=public_cert.read_bytes())

    def exercise_pairing(self, process, events, code):
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/api/screen')[0], 401)
        self.assertEqual(self.request('POST', '/api/login', {'code': code}, origin=False)[0], 403)
        for invalid in (1234, '123', '12345', 'abcd', '１２３４', '123\n', None):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.request('POST', '/api/login', {'code': invalid})[0], 400)

        wrong = '0000' if code != '0000' else '1111'
        for expected in (401, 401, 401, 401, 429):
            self.assertEqual(self.request('POST', '/api/login', {'code': wrong})[0], expected)
        self.assertEqual(events.get(timeout=5), {'event': 'exhausted'})
        self.assertEqual(self.request('POST', '/api/login', {'code': code})[0], 429)

        process.stdin.write('pair\n')
        process.stdin.flush()
        renewed = events.get(timeout=5)
        self.assertEqual(renewed['event'], 'code')
        self.assertNotEqual(code, renewed['code'])
        self.assertEqual(self.request('POST', '/api/login', {'code': code})[0], 401)
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: self.request('POST', '/api/login', {'code': renewed['code']}), range(2)))
        self.assertEqual(sorted(outcome[0] for outcome in outcomes), [200, 410])
        self.assertEqual(events.get(timeout=5), {'event': 'paired'})

        headers = next(headers for status, headers, _ in outcomes if status == 200)
        cookie = headers['Set-Cookie']
        for attribute in ('HttpOnly', 'Secure', 'SameSite=Strict'):
            self.assertIn(attribute, cookie)
        cookie = cookie.split(';')[0]
        status, headers, body = self.request('GET', '/api/screen', cookie=cookie)
        self.assertEqual((status, headers['Content-Type'], body), (200, 'image/jpeg', b'jpeg-test'))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(self.request('POST', '/api/action', {'type': 'click', 'x': 12, 'y': 13}, cookie)[0], 200)
        self.assertEqual(self.request('POST', '/api/logout', {}, cookie)[0], 200)
        self.assertEqual(self.request('GET', '/api/screen', cookie=cookie)[0], 401)

    def exercise_app_capture(self, process, events, code):
        self.assertEqual(self.request('GET', '/api/screen')[0], 401)
        click = {'type': 'click', 'x': 12.2, 'y': 13}
        self.assertEqual(self.request('POST', '/api/action', click)[0], 401)
        status, headers, _ = self.request('POST', '/api/login', {'code': code})
        self.assertEqual(status, 200)
        self.assertEqual(events.get(timeout=5), {'event': 'paired'})
        cookie = headers['Set-Cookie'].split(';')[0]

        def reply(message):
            process.stdin.write(json.dumps(message) + '\n')
            process.stdin.flush()

        with ThreadPoolExecutor(max_workers=1) as worker:
            response = worker.submit(self.request, 'GET', '/api/screen', cookie=cookie)
            capture = events.get(timeout=5)
            self.assertEqual(capture['event'], 'capture')
            screenshot = Path(capture['path'])
            self.assertEqual(screenshot.name, 'screen.jpg')
            self.assertEqual(screenshot.parent.stat().st_mode & 0o777, 0o700)

            reply({'capture': 'different-id', 'error': None})
            process.stdin.write('pair\n')
            process.stdin.flush()
            self.assertEqual(events.get(timeout=5)['event'], 'code')
            self.assertFalse(response.done())

            screenshot.write_bytes(b'app-screen-image')
            reply({'capture': capture['id'], 'error': None})
            status, headers, body = response.result(timeout=5)
            self.assertEqual((status, headers['Content-Type'], body), (200, 'image/jpeg', b'app-screen-image'))
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertFalse(screenshot.parent.exists())

            response = worker.submit(self.request, 'GET', '/api/screen', cookie=cookie)
            capture = events.get(timeout=5)
            reply({'capture': capture['id'], 'error': '화면 보기 권한을 허용해줘.'})
            status, _, body = response.result(timeout=5)
            self.assertEqual((status, json.loads(body)['error']), (503, '화면 보기 권한을 허용해줘.'))

            self.assertEqual(self.request('POST', '/api/action', {'type': 'type', 'text': '\ud800'}, cookie)[0], 400)
            response = worker.submit(self.request, 'POST', '/api/action', click, cookie)
            operation = events.get(timeout=5)
            self.assertEqual(operation['event'], 'input')
            self.assertEqual(operation['args'], ['click', '12', '13'])
            self.assertGreater(operation['expiresAt'], time.time())
            self.assertEqual(self.request('POST', '/api/action', click, cookie)[0], 503)
            reply({'input': 'different-id', 'error': None})
            reply({'input': operation['id'], 'error': None})
            self.assertEqual(response.result(timeout=5)[0], 200)

            response = worker.submit(self.request, 'POST', '/api/action', click, cookie)
            operation = events.get(timeout=5)
            reply({'input': operation['id'], 'error': '클릭·입력 권한을 허용해줘.'})
            status, _, body = response.result(timeout=5)
            self.assertEqual((status, json.loads(body)['error']), (503, '클릭·입력 권한을 허용해줘.'))

    def request(self, method, path, body=None, cookie=None, origin=True):
        headers = {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        if cookie is not None:
            headers['Cookie'] = cookie
        if origin:
            headers['Origin'] = f'https://127.0.0.1:{self.port}'
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=5, context=self.context)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()
