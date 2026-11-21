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
                return req.fulfill(json={'authenticated': getattr(self, 'authenticated', True), 'platform': getattr(self, 'platform', 'mac')})
            if path == '/api/login':
                self.logins = getattr(self, 'logins', []) + [json.loads(req.request.post_data)]
                return req.fulfill(json={'ok': True})
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

    def wait_for_actions(self, count):
        for _ in range(60):
            if len(self.actions) >= count:
                return
            self.page.wait_for_timeout(50)
        self.fail(f'expected {count} actions, got {self.actions}')

    def click_screen_with_mouse(self):
        screen = self.page.locator('#screen')
        expect(screen).to_be_visible()
        rect = screen.bounding_box()
        self.page.mouse.click(rect['x'] + 10, rect['y'] + 10)
        self.wait_for_actions(1)
        return rect

    def test_mouse_wheel_scrolls_and_batches_fast_wheel_events(self):
        expect(self.page.locator('#screen')).to_be_visible()
        rect = self.page.locator('#screen').bounding_box()
        self.page.mouse.move(rect['x'] + 20, rect['y'] + 20)
        self.page.evaluate('''() => {
          const screen = document.getElementById('screen');
          for (const deltaY of [100, 50]) screen.dispatchEvent(new WheelEvent('wheel', {deltaY, bubbles: true, cancelable: true}));
        }''')
        self.wait_for_actions(1)
        self.page.mouse.wheel(0, -100)
        self.wait_for_actions(2)
        self.assertEqual(self.actions, [{'type': 'scroll', 'dy': 300}, {'type': 'scroll', 'dy': -200}])

    def test_desktop_keyboard_sends_text_keys_and_korean_composition(self):
        self.click_screen_with_mouse()
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'keyCatcher')
        self.page.keyboard.type('ab c')
        self.page.keyboard.press('Enter')
        self.page.keyboard.press('Backspace')
        self.page.keyboard.press('Control+a')
        self.page.evaluate('''() => {
          const field = document.getElementById('keyCatcher');
          field.dispatchEvent(new CompositionEvent('compositionstart'));
          field.value = '한';
          field.dispatchEvent(new InputEvent('input', {isComposing: true}));
          field.dispatchEvent(new CompositionEvent('compositionend', {data: '한'}));
        }''')
        self.wait_for_actions(8)
        typed = [action for action in self.actions[1:]]
        self.assertEqual(typed, [
            {'type': 'type', 'text': 'a'}, {'type': 'type', 'text': 'b'}, {'type': 'type', 'text': ' '}, {'type': 'type', 'text': 'c'},
            {'type': 'key', 'key': 'enter'}, {'type': 'key', 'key': 'backspace'}, {'type': 'type', 'text': '한'},
        ])

    def test_touch_tap_does_not_open_the_phone_keyboard(self):
        screen = self.page.locator('#screen')
        expect(screen).to_be_visible()
        rect = screen.bounding_box()
        with self.page.expect_response(lambda resp: resp.url.endswith('/api/action')):
            self.page.touchscreen.tap(rect['x'] + 20, rect['y'] + 20)
        self.assertNotEqual(self.page.evaluate('document.activeElement.id'), 'keyCatcher')

    def test_windows_host_uses_pc_labels(self):
        self.platform = 'windows'
        self.page.reload()
        expect(self.page.locator('.screen-toolbar span').first).to_have_text('PC 화면')
        expect(self.page.locator('#screen')).to_have_attribute('alt', 'PC 화면. 누르면 해당 위치를 클릭합니다.')

    def test_fourth_digit_connects_without_pressing_the_button(self):
        self.authenticated = False
        self.page.reload()
        expect(self.page.locator('#code')).to_be_focused()
        self.page.keyboard.type('12a3')
        self.assertEqual(getattr(self, 'logins', []), [])
        self.page.keyboard.type('4')
        for _ in range(40):
            if getattr(self, 'logins', []):
                break
            self.page.wait_for_timeout(50)
        self.assertEqual(self.logins, [{'code': '1234'}])

    def test_tools_are_tabs_and_full_screen_mode_keeps_the_screen_usable(self):
        expect(self.page.locator('#screen')).to_be_visible()
        expect(self.page.locator('#panelText')).to_be_visible()
        expect(self.page.locator('#panelKeys')).to_be_hidden()
        self.page.click('#tabKeys')
        expect(self.page.locator('#panelKeys')).to_be_visible()
        expect(self.page.locator('#panelText')).to_be_hidden()
        self.assertEqual(self.page.get_attribute('#tabKeys', 'aria-selected'), 'true')
        self.page.click('#immersiveButton')
        expect(self.page.locator('body')).to_have_class('immersive')
        expect(self.page.locator('#toolPanel')).to_be_hidden()
        self.page.click('#toolsButton')
        expect(self.page.locator('#toolPanel')).to_be_visible()
        with self.page.expect_response(lambda resp: resp.url.endswith('/api/action')):
            self.page.click('[data-key="enter"]')
        self.assertEqual(self.actions, [{'type': 'key', 'key': 'enter'}])
        self.page.click('#immersiveButton')
        self.assertEqual(self.page.evaluate('document.body.className'), '')
        expect(self.page.locator('#toolPanel')).to_be_visible()

    def test_a_click_shows_a_ripple_where_it_was_tapped(self):
        rect = self.click_screen_with_mouse()
        position = self.page.evaluate('({left: ripple.style.left, top: ripple.style.top, go: ripple.classList.contains("go")})')
        self.assertTrue(position['go'])
        self.assertTrue(position['left'].endswith('px') and position['top'].endswith('px'))

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
