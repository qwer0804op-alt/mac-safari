import http.client
import itertools
import os
import re
import shutil
import ssl
import sys
import tempfile
import threading
import time
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import certificates  # noqa: E402
import screen_stream  # noqa: E402


def load_server():
    spec = spec_from_file_location('mac_remote_server_stream', ROOT / 'server.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HubTest(unittest.TestCase):
    def test_fps_is_limited_to_sixty(self):
        self.assertEqual([screen_stream.clamp_fps(v) for v in (None, 'x', 0, -5, 1, 30, 60, 999)], [30, 30, 1, 1, 1, 30, 60, 60])

    def test_capture_runs_only_while_someone_watches_and_unchanged_frames_are_not_new(self):
        counter = itertools.count()
        calls = []

        def capture():
            calls.append(1)
            return b'same' if len(calls) > 3 else b'frame-%d' % next(counter)

        hub = screen_stream.FrameHub(capture)
        viewer = hub.subscribe(60)
        seq, frame = hub.wait_first(2)
        self.assertTrue(frame.startswith(b'frame-'))
        deadline = time.monotonic() + 2
        while len(calls) < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
        update = hub.wait_next(hub.seq, timeout=0.05)
        self.assertFalse(update[2], 'an unchanged screen must not count as a new frame')
        hub.unsubscribe(viewer)
        time.sleep(0.4)
        count = len(calls)
        time.sleep(0.3)
        self.assertEqual(len(calls), count, 'capturing must stop when nobody is watching')

    def test_capture_errors_reach_the_first_waiter(self):
        def capture():
            raise RuntimeError('권한을 허용해 주세요.')

        hub = screen_stream.FrameHub(capture)
        hub.subscribe(30)
        with self.assertRaisesRegex(RuntimeError, '권한을 허용해 주세요.'):
            hub.wait_first(3)
        hub.stop()
        with self.assertRaises(RuntimeError):
            hub.subscribe(30)


class ServerFixture:
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        module = load_server()
        store = certificates.CertificateStore(Path(self.folder.name) / 'certs')
        self.context = store.prepare([])
        self.ca = store.ca_pem
        state = module.RemoteState(lambda event: None)
        state.sessions['token'] = time.monotonic() + 600
        self.server = module.RemoteHTTPServer(('127.0.0.1', 0), self.context, state)
        self.counter = itertools.count()
        self.fail_capture = None
        self.server.frames.capture = self.capture
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.frames.stop)

    def capture(self):
        if self.fail_capture:
            raise RuntimeError(self.fail_capture)
        return b'\xff\xd8' + str(next(self.counter)).encode().ljust(2000, b'x') + b'\xff\xd9'

    def connect(self, path, cookie='mac_remote=token'):
        client = ssl.create_default_context(cafile=str(self.ca))
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=10, context=client)
        connection.request('GET', path, headers={'Cookie': cookie} if cookie else {})
        return connection, connection.getresponse()

    def read_parts(self, response, seconds):
        parts, buffer, end = [], b'', time.monotonic() + seconds
        while time.monotonic() < end:
            chunk = response.read1(65536)
            if not chunk:
                break
            buffer += chunk
            while True:
                marker = buffer.find(b'\r\n\r\n')
                if marker < 0:
                    break
                head = buffer[:marker].decode()
                length = int(next(line.split(':')[1] for line in head.split('\r\n') if line.lower().startswith('content-length')))
                if len(buffer) < marker + 4 + length + 2:
                    break
                parts.append((head, buffer[marker + 4:marker + 4 + length]))
                buffer = buffer[marker + 4 + length + 2:]
        return parts


class StreamEndpointTest(ServerFixture, unittest.TestCase):
    def test_requires_a_session(self):
        connection, response = self.connect('/api/stream?fps=60', cookie=None)
        self.assertEqual(response.status, 401)
        connection.close()

    def test_stream_is_a_multipart_jpeg_sequence_that_can_run_near_sixty_fps(self):
        connection, response = self.connect('/api/stream?fps=60')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'multipart/x-mixed-replace; boundary=frame')
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        started = time.monotonic()
        parts = self.read_parts(response, 1.5)
        elapsed = time.monotonic() - started
        connection.close()
        self.assertTrue(all('image/jpeg' in head and body.startswith(b'\xff\xd8') for head, body in parts))
        rate = len(parts) / elapsed
        print(f'\nmeasured stream rate with an instant capture: {rate:.1f} fps', file=sys.stderr)
        self.assertGreater(rate, 25)
        self.assertLess(rate, 70, 'the requested 60 fps must act as an upper limit')

    def test_requested_fps_is_respected_and_capped(self):
        connection, response = self.connect('/api/stream?fps=10')
        started = time.monotonic()
        parts = self.read_parts(response, 1.5)
        rate = len(parts) / (time.monotonic() - started)
        connection.close()
        self.assertTrue(6 < rate < 14, rate)
        connection, response = self.connect('/api/stream?fps=100000')
        started = time.monotonic()
        parts = self.read_parts(response, 1.0)
        rate = len(parts) / (time.monotonic() - started)
        connection.close()
        self.assertLess(rate, 70)

    def test_capture_failure_before_the_first_frame_is_a_json_error(self):
        self.fail_capture = '화면 보기 권한을 허용해 주세요.'
        connection, response = self.connect('/api/stream')
        self.assertEqual(response.status, 503)
        self.assertIn('화면 보기 권한을 허용해 주세요.', response.read().decode())
        connection.close()

    def test_logout_ends_the_stream(self):
        connection, response = self.connect('/api/stream?fps=30')
        self.assertEqual(response.status, 200)
        self.read_parts(response, 0.2)
        self.server.state.sessions.clear()
        parts_after = self.read_parts(response, 1.0)
        connection.close()
        self.assertLess(len(parts_after), 40)
        deadline = time.monotonic() + 3
        while self.server.frames.viewers and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.server.frames.viewers)


try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None
CHROME = os.environ.get('CHROMIUM') or shutil.which('chromium')


@unittest.skipUnless(sync_playwright and CHROME, 'requires Playwright and Chromium')
class BrowserStreamTest(ServerFixture, unittest.TestCase):
    FRAME = (ROOT / 'tests' / 'fixtures' / 'frame.jpg').read_bytes()

    def capture(self):
        # A valid JPEG that differs on every call (bytes after the end marker are ignored).
        return self.FRAME + str(next(self.counter)).encode()

    def open_page(self, play, **options):
        browser = play.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
        context = browser.new_context(ignore_https_errors=True, viewport={'width': 1280, 'height': 900})
        context.add_cookies([{'name': 'mac_remote', 'value': 'token', 'url': f'https://127.0.0.1:{self.port}'}])
        page = context.new_page()
        page.goto(f'https://127.0.0.1:{self.port}/')
        return browser, page

    def test_browser_shows_the_live_stream_and_reports_its_frame_rate(self):
        with sync_playwright() as play:
            browser, page = self.open_page(play)
            try:
                expect(page.locator('#fpsSelect')).to_have_value('60')
                expect(page.locator('#screen')).to_have_attribute('src', re.compile('^blob:'), timeout=10000)
                expect(page.locator('#lastUpdate')).to_have_text(re.compile(r'실시간 \d+fps'), timeout=10000)
                page.wait_for_timeout(2500)
                rate = int(re.search(r'(\d+)fps', page.locator('#lastUpdate').text_content()).group(1))
                print(f'\nbrowser displayed {rate} fps from the stream', file=sys.stderr)
                self.assertGreater(rate, 20)
                self.assertEqual(page.evaluate('lastImageSize.width'), 480)
                page.select_option('#fpsSelect', '15')
                page.wait_for_timeout(3500)
                slow = int(re.search(r'(\d+)fps', page.locator('#lastUpdate').text_content()).group(1))
                self.assertLess(slow, 20)
                self.assertEqual(page.evaluate('localStorage.getItem("macRemoteFps")'), '15')
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
