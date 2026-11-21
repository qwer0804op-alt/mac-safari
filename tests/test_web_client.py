"""Run these browser regressions with Playwright and a local Chromium installation."""
import json
import os
from pathlib import Path
import shutil
import unittest

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get('CHROMIUM') or shutil.which('chromium')


@unittest.skipUnless(sync_playwright and CHROME, 'requires Playwright and Chromium')
class BrowserRegression(unittest.TestCase):
    def setUp(self):
        self.play = sync_playwright().start()
        self.addCleanup(self.play.stop)
        browser = self.play.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
        self.addCleanup(browser.close)
        self.page = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
        self.actions = []
        self.status_count = 0

        def route(req):
            path = req.request.url.partition('.test')[2]
            if path == '/api/status':
                self.status_count += 1
                if self._testMethodName == 'test_status_recovers_on_online' and self.status_count == 1:
                    return req.abort()
                return req.fulfill(json={'authenticated': True})
            if path == '/api/screen':
                return req.fulfill(path=ROOT / 'public/icon-192.png', content_type='image/png')
            if path == '/api/action':
                self.actions.append(json.loads(req.request.post_data))
                return req.fulfill(json={'ok': True})
            if path in ('/', '/app.js', '/style.css'):
                return req.fulfill(path=ROOT / 'public' / (path.lstrip('/') or 'index.html'))
            return req.fulfill(status=404)
        self.page.route('**/*', route)
        self.page.goto('https://remote.test')

    def test_status_recovers_on_online(self):
        expect(self.page.locator('#loginMessage')).to_contain_text('연결할 수 없습니다')
        self.page.evaluate("window.dispatchEvent(new Event('online'))")

        expect(self.page.locator('#screen')).to_be_visible()

    def test_tap_drag_and_release_outside(self):
        screen = self.page.locator('#screen')
        expect(screen).to_be_visible()
        rect = screen.bounding_box()
        left, top = rect['x'], rect['y']
        mouse = self.page.mouse
        mouse.move(left + 2, top + 12)
        mouse.down()
        mouse.move(left - 2, top + 12)
        mouse.up()
        mouse.move(left + 40, top + 40)
        mouse.down()
        mouse.move(left + 80, top + 40)
        mouse.move(left + 40, top + 40)
        mouse.up()
        with self.page.expect_response(lambda resp: resp.url.endswith('/api/action')):
            self.page.touchscreen.tap(left + rect['width']/2, top + rect['height']/2)
        self.assertEqual(self.actions, [{'type': 'click', 'x': 96, 'y': 96}])

    def test_background_discards_queued_input(self):
        expect(self.page.locator('#screen')).to_be_visible()
        pending = []
        self.page.route('**/api/action', lambda route: pending.append(route))
        with self.page.expect_request(lambda req: req.url.endswith('/api/action')):
            self.page.evaluate("""() => {
          sendAction({type:'click',x:4,y:5});
          sendAction({type:'click',x:6,y:7});
        }""")
        self.page.wait_for_function('typeof actionQueue !== "undefined"')
        self.assertEqual(len(pending), 1)
        self.page.evaluate("""() => {
          Object.defineProperty(document, 'hidden', {value:true, configurable:true});
          document.dispatchEvent(new Event('visibilitychange'));
          delete document.hidden;
          document.dispatchEvent(new Event('visibilitychange'));
        }""")
        self.page.unroute('**/api/action')
        pending[0].fulfill(json={'ok': True})
        self.page.evaluate('actionQueue')
        self.assertEqual(self.actions, [])
