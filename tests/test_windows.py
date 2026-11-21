import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import certificates  # noqa: E402
import windows_app  # noqa: E402
import windows_helper as helper  # noqa: E402


class FakeBackend:
    def __init__(self, size=(1920, 1080)):
        self.size = size
        self.calls = []

    def screen_size(self):
        return self.size

    def screenshot(self, path):
        self.calls.append(('screenshot', path))

    def click(self, x, y):
        self.calls.append(('click', x, y))

    def scroll(self, wheel):
        self.calls.append(('scroll', wheel))

    def key(self, vk):
        self.calls.append(('key', vk))

    def unicode_unit(self, unit):
        self.calls.append(('unicode', unit))


class WindowsHelperTest(unittest.TestCase):
    def run_helper(self, *args):
        backend = FakeBackend()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            helper.run(list(args), backend)
        return backend.calls, output.getvalue()

    def test_win32_input_structure_matches_64_bit_windows_layout(self):
        self.assertEqual(ctypes.sizeof(helper.MOUSEINPUT), 32)
        self.assertEqual(ctypes.sizeof(helper.KEYBDINPUT), 24)
        self.assertEqual(ctypes.sizeof(helper.INPUT), 40)

    def test_click_uses_screenshot_pixels_and_rejects_points_outside_the_screen(self):
        self.assertEqual(self.run_helper('click', '12', '13')[0], [('click', 12, 13)])
        for x, y in (('-1', '0'), ('1920', '0'), ('0', '1080'), ('nan', '1'), ('x', '1')):
            with self.assertRaises(helper.HelperError):
                self.run_helper('click', x, y)

    def test_scroll_down_is_a_negative_wheel_delta(self):
        self.assertEqual(self.run_helper('scroll', '400')[0], [('scroll', -480)])
        self.assertEqual(self.run_helper('scroll', '-400')[0], [('scroll', 480)])
        self.assertEqual(self.run_helper('scroll', '1')[0], [('scroll', -1)])
        for value in ('0', '1001', 'abc'):
            with self.assertRaises(helper.HelperError):
                self.run_helper('scroll', value)

    def test_keys_match_the_names_the_server_allows(self):
        names = ['enter', 'tab', 'escape', 'backspace', 'delete', 'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', 'space', 'a', 'Z', '7']
        codes = [self.run_helper('key', name)[0][0][1] for name in names]
        self.assertEqual(codes[:3], [0x0D, 0x09, 0x1B])
        self.assertEqual(codes[-3:], [ord('A'), ord('Z'), ord('7')])
        with self.assertRaises(helper.HelperError):
            self.run_helper('key', 'f13')

    def test_type_sends_utf16_units_and_translates_line_breaks(self):
        calls, _ = self.run_helper('type', 'a한\n😀\r\tb')
        self.assertEqual(calls, [('unicode', ord('a')), ('unicode', ord('한')), ('key', 0x0D),
                                 ('unicode', 0xD83D), ('unicode', 0xDE00), ('key', 0x09), ('unicode', ord('b'))])
        for text in ('', 'a\x00b', 'x' * 4097):
            with self.assertRaises(helper.HelperError):
                self.run_helper('type', text)

    def test_screenshot_reports_the_size_and_unknown_commands_are_usage_errors(self):
        calls, output = self.run_helper('screenshot', 'out.jpg')
        self.assertEqual((calls, json.loads(output)), ([('screenshot', 'out.jpg')], {'width': 1920, 'height': 1080}))
        for args in ((), ('click', '1'), ('dance',)):
            with self.assertRaises(helper.UsageError):
                self.run_helper(*args)


class WindowsLauncherTest(unittest.TestCase):
    def test_internet_approval_flow(self):
        console = windows_app.Console(internet=True)
        lines = console.handle({'event': 'ready', 'urls': ['https://example.trycloudflare.com'], 'code': '0042'})
        self.assertIn('접속 주소: https://example.trycloudflare.com', lines)
        self.assertIn('4자리 코드: 0042', lines)
        self.assertEqual(console.command('y'), (None, '지금 승인할 연결 요청이 없습니다.'))
        console.handle({'event': 'approval', 'id': 'abc'})
        self.assertEqual(console.command('Y'), ('approve abc', None))
        self.assertEqual(console.command('y')[0], None)
        console.handle({'event': 'approval', 'id': 'def'})
        self.assertEqual(console.command('n'), ('deny def', None))

    def test_commands_and_renewed_code(self):
        console = windows_app.Console(internet=False)
        self.assertEqual(console.command('c'), ('pair', None))
        self.assertEqual(console.command(' q '), ('quit', None))
        self.assertEqual(console.command('?'), (None, windows_app.HELP))
        self.assertEqual(console.command(''), (None, None))
        console.handle({'event': 'approval', 'id': 'abc'})
        console.handle({'event': 'code', 'code': '1234'})
        self.assertIsNone(console.approval)
        self.assertEqual(console.handle({'event': 'unknown'}), [])


class PlatformSupportTest(unittest.TestCase):
    def test_openssl_can_be_chosen_with_an_environment_variable(self):
        fake = ROOT / 'tests' / 'test_windows.py'
        with patch.object(sys, 'platform', 'linux'), patch.dict(os.environ, {'MACREMOTE_OPENSSL': str(fake)}):
            self.assertEqual(certificates.openssl_path(), str(fake))

    def test_only_reachable_ipv4_addresses_are_advertised(self):
        usable = [certificates.usable_address(value) for value in ('192.168.0.5', '10.0.0.2', '127.0.0.1', '169.254.1.1', '0.0.0.0', '224.0.0.1', 'bad')]
        self.assertEqual(usable, [True, True, False, False, False, False, False])
        with patch.object(sys, 'platform', 'win32'):
            for value in certificates.local_addresses():
                self.assertTrue(certificates.usable_address(value))


class WindowsServerTest(unittest.TestCase):
    def load_server(self):
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location('mac_remote_server_windows', ROOT / 'server.py')
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_windows_uses_the_python_helper_and_reports_its_platform(self):
        with patch.object(sys, 'platform', 'win32'):
            server = self.load_server()
        self.assertEqual(server.HELPER_COMMAND, [sys.executable, '-I', str(ROOT / 'windows_helper.py')])
        self.assertTrue(server.HELPER.is_file())

    def test_helper_errors_reach_the_client_message(self):
        import tempfile
        server = self.load_server()
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'fake_helper.py'
            script.write_text('import sys\nprint("화면을 못 가져왔습니다.", file=sys.stderr)\nsys.exit(1)\n', encoding='utf-8')
            with patch.object(server, 'HELPER_COMMAND', [sys.executable, str(script)]):
                with self.assertRaisesRegex(RuntimeError, '화면을 못 가져왔습니다'):
                    server.run_helper(['click', '1', '1'])


if __name__ == '__main__':
    unittest.main()
