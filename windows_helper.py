#!/usr/bin/env python3
"""Windows control helper for Mac Remote. It takes the same commands as the macOS helper.

Usage: windows_helper.py screenshot <path> | click <x> <y> | scroll <dy> | key <name> | type <text>
Only the Python standard library is required. Pillow speeds up screenshots when it is installed.
"""
import base64
import ctypes
import json
import os
import subprocess
import sys

WHEEL_DELTA = 120
WHEEL_PER_SCROLL = 1.2
MAX_TEXT_UNITS = 4096
CREATE_NO_WINDOW = 0x08000000

VIRTUAL_KEYS = {
    'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'space': 0x20, 'backspace': 0x08,
    'escape': 0x1B, 'esc': 0x1B, 'delete': 0x2E, 'forwarddelete': 0x2E,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
}
EXTENDED_KEYS = {0x2E, 0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22}
VK_RETURN, VK_TAB = 0x0D, 0x09

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_WHEEL = 0x0002, 0x0004, 0x0800
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004

CAPTURE_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace MacRemote -Name Dpi -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();'
[void][MacRemote.Dpi]::SetProcessDPIAware()
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
$graphics.Dispose()
$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
$parameters = New-Object System.Drawing.Imaging.EncoderParameters(1)
$parameters.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]65)
$bitmap.Save($env:MACREMOTE_OUT, $codec, $parameters)
$bitmap.Dispose()
'''


class HelperError(Exception):
    pass


class UsageError(HelperError):
    pass


def number(value, message):
    try:
        result = float(value)
    except ValueError:
        raise HelperError(message) from None
    if result != result or result in (float('inf'), float('-inf')):
        raise HelperError(message)
    return result


def utf16_units(text):
    data = text.encode('utf-16-le', 'surrogatepass')
    return [int.from_bytes(data[index:index + 2], 'little') for index in range(0, len(data), 2)]


def key_code(name):
    name = name.lower()
    if name in VIRTUAL_KEYS:
        return VIRTUAL_KEYS[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    raise HelperError('지원하지 않는 키입니다.')


def scroll_wheel_data(amount):
    # The web client sends positive deltas for scrolling down; Windows uses negative for down.
    value = -round(amount * WHEEL_PER_SCROLL)
    if value == 0:
        value = -1 if amount > 0 else 1
    return value


def run(args, backend):
    command = args[0] if args else None
    if command == 'screenshot' and len(args) == 2 and args[1]:
        backend.screenshot(args[1])
        width, height = backend.screen_size()
        print(json.dumps({'width': width, 'height': height}))
    elif command == 'permissions' and len(args) == 1:
        print(json.dumps({'accessibility': True, 'screenCapture': True}))
    elif command == 'click' and len(args) == 3:
        x = number(args[1], 'Windows 화면에 있는 위치를 다시 눌러 주세요.')
        y = number(args[2], 'Windows 화면에 있는 위치를 다시 눌러 주세요.')
        width, height = backend.screen_size()
        if not (width > 0 and height > 0 and 0 <= x < width and 0 <= y < height):
            raise HelperError('Windows 화면에 있는 위치를 다시 눌러 주세요.')
        backend.click(int(x), int(y))
    elif command == 'scroll' and len(args) == 2:
        try:
            amount = int(args[1])
        except ValueError:
            raise HelperError('스크롤 값이 올바르지 않습니다.') from None
        if amount == 0 or not -1000 <= amount <= 1000:
            raise HelperError('스크롤 값이 올바르지 않습니다.')
        backend.scroll(scroll_wheel_data(amount))
    elif command == 'key' and len(args) == 2:
        backend.key(key_code(args[1]))
    elif command == 'type' and len(args) == 2:
        text = args[1]
        units = utf16_units(text)
        if not text or len(units) > MAX_TEXT_UNITS or '\x00' in text:
            raise HelperError('보낼 텍스트의 길이를 확인해 주세요.')
        for index, unit in enumerate(units):
            if unit == 0x0A:
                backend.key(VK_RETURN)
            elif unit == 0x09:
                backend.key(VK_TAB)
            elif unit != 0x0D:
                backend.unicode_unit(unit)
    else:
        raise UsageError('Usage: windows_helper.py screenshot <path> | click <x> <y> | scroll <dy> | key <name> | type <text>')


# Fixed-width types keep the layout identical to the Win32 INPUT structure on any platform.
DWORD, WORD, LONG = ctypes.c_uint32, ctypes.c_uint16, ctypes.c_int32
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', LONG), ('dy', LONG), ('mouseData', DWORD),
                ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', WORD), ('wScan', WORD), ('dwFlags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', DWORD), ('wParamL', WORD), ('wParamH', WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('union',)
    _fields_ = [('type', DWORD), ('union', INPUT_UNION)]


class Win32Backend:
    def __init__(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.user32.SetCursorPos.restype = ctypes.c_int
        self.user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        self.user32.GetSystemMetrics.restype = ctypes.c_int
        # Screenshot pixels and cursor positions must use the same physical pixels.
        try:
            ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)
        except (OSError, AttributeError):
            self.user32.SetProcessDPIAware()

    def screen_size(self):
        return self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)

    def send(self, *events):
        array = (INPUT * len(events))(*events)
        if self.user32.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
            raise HelperError('Windows에 입력을 보낼 수 없습니다. 관리자 권한 앱이나 잠금 화면에는 입력할 수 없습니다.')

    def mouse(self, flags, data=0):
        event = INPUT(type=INPUT_MOUSE)
        event.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        return event

    def keyboard(self, vk=0, scan=0, flags=0):
        event = INPUT(type=INPUT_KEYBOARD)
        event.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        return event

    def click(self, x, y):
        if not self.user32.SetCursorPos(x, y):
            raise HelperError('Windows에 입력을 보낼 수 없습니다. 다시 시도해 주세요.')
        self.send(self.mouse(MOUSEEVENTF_LEFTDOWN), self.mouse(MOUSEEVENTF_LEFTUP))

    def scroll(self, wheel):
        self.send(self.mouse(MOUSEEVENTF_WHEEL, wheel))

    def key(self, vk):
        extended = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_KEYS else 0
        self.send(self.keyboard(vk=vk, flags=extended), self.keyboard(vk=vk, flags=extended | KEYEVENTF_KEYUP))

    def unicode_unit(self, unit):
        self.send(self.keyboard(scan=unit, flags=KEYEVENTF_UNICODE),
                  self.keyboard(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))

    def screenshot(self, path):
        try:
            from PIL import ImageGrab
        except ImportError:
            return self.screenshot_with_powershell(path)
        try:
            ImageGrab.grab().convert('RGB').save(path, 'JPEG', quality=65)
        except OSError:
            raise HelperError('Windows 화면을 캡처할 수 없습니다. 화면이 잠겨 있지 않은지 확인해 주세요.') from None

    def screenshot_with_powershell(self, path):
        encoded = base64.b64encode(CAPTURE_SCRIPT.encode('utf-16-le')).decode('ascii')
        environment = dict(os.environ, MACREMOTE_OUT=path)
        try:
            proc = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                capture_output=True, env=environment, timeout=12, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise HelperError('Windows 화면을 캡처할 수 없습니다. PowerShell을 확인해 주세요.') from None
        if proc.returncode or not os.path.isfile(path):
            raise HelperError('Windows 화면을 캡처할 수 없습니다. 화면이 잠겨 있지 않은지 확인해 주세요.')

    def close(self):
        pass


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    try:
        if os.name != 'nt':
            raise HelperError('이 도구는 Windows에서만 쓸 수 있습니다.')
        run(argv if argv is not None else sys.argv[1:], Win32Backend())
    except UsageError as error:
        print(error, file=sys.stderr)
        return 2
    except HelperError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
