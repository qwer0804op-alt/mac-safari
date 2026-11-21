"""Run an opt-in Cloudflare Quick Tunnel for the app's loopback HTTPS server."""
from contextlib import contextmanager
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time


QUICK_URL = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com(?=[\s|]|$)')


def cloudflared_path():
    if os.name == 'nt':
        candidates = [shutil.which('cloudflared')]
        for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            base = os.environ.get(variable)
            if base:
                candidates += [str(Path(base) / 'cloudflared' / 'cloudflared.exe'), str(Path(base) / 'Microsoft' / 'WinGet' / 'Links' / 'cloudflared.exe')]
        for path in candidates:
            if path and Path(path).is_file():
                return path
        raise RuntimeError('인터넷 연결에 cloudflared가 필요합니다. 명령 프롬프트에서 winget install Cloudflare.cloudflared로 설치해 주세요.')
    for path in ('/opt/homebrew/bin/cloudflared', '/usr/local/bin/cloudflared', '/opt/local/bin/cloudflared'):
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    raise RuntimeError('인터넷 연결에 cloudflared가 필요합니다. Mac에서 brew install cloudflared로 설치해 주세요.')


@contextmanager
def quick_tunnel(port, ca_pem, stopped):
    # A closed file in a temporary directory can be read by cloudflared on Windows too.
    with tempfile.TemporaryDirectory(prefix='macremote-tunnel-') as directory:
        config = Path(directory) / 'config.yml'
        config.write_text('{}\n', encoding='ascii')
        with _run_quick_tunnel(port, ca_pem, stopped, str(config)) as tunnel:
            yield tunnel


@contextmanager
def _run_quick_tunnel(port, ca_pem, stopped, config):
    with subprocess.Popen(
        [
            cloudflared_path(), 'tunnel', '--config', config, '--no-autoupdate',
            '--metrics', '127.0.0.1:0', '--url', f'https://127.0.0.1:{port}',
            '--origin-ca-pool', str(ca_pem), '--origin-server-name', '127.0.0.1',
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    ) as process:
        signals = queue.SimpleQueue()

        def watch_output():
            url = None
            registered = False
            for line in process.stdout:
                if not url:
                    match = QUICK_URL.search(line)
                    if match:
                        url = match.group()
                        signals.put(url)
                if not registered and 'Registered tunnel connection' in line:
                    registered = True
                    signals.put(True)
            signals.put(None)

        threading.Thread(target=watch_output, daemon=True).start()
        try:
            deadline = time.monotonic() + 35
            address = None
            registered = False
            while not stopped.is_set() and time.monotonic() < deadline:
                try:
                    signal = signals.get(timeout=0.2)
                except queue.Empty:
                    continue
                if signal is None:
                    break
                if signal is True:
                    registered = True
                else:
                    address = signal
                if address and registered:
                    yield address, process
                    return
            if stopped.is_set():
                yield None, process
                return
            raise RuntimeError('인터넷 주소를 열지 못했습니다. Mac의 인터넷 연결을 확인하고 다시 시도해 주세요.')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
