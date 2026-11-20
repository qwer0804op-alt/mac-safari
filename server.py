#!/usr/bin/env python3
"""Authenticated, local-network remote control for the Mac running this server."""
import argparse
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from certificates import CertificateStore, local_addresses
from internet_tunnel import quick_tunnel

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
HELPER = ROOT / 'native' / 'remote-helper'
MAX_BODY = 16384
SESSION_SECONDS = 12 * 60 * 60
INTERNET_SESSION_SECONDS = 60 * 60
PAIR_SECONDS = 5 * 60
PAIR_ATTEMPTS = 5
APPROVAL_SECONDS = 50
CAPTURE_SECONDS = 8
CAPTURE_TIMEOUT = 'Mac에서 화면을 가져오는 데 시간이 너무 오래 걸렸어.'
CAPTURE_STOPPED = 'Mac에서 화면 공유를 종료했어.'
INPUT_SECONDS = 5
INPUT_TIMEOUT = 'Mac이 입력 결과를 보내지 않았어. 다시 보내기 전에 Mac 화면을 확인해.'
INPUT_STOPPED = 'Mac에서 원격 입력을 종료했어.'
helper_lock = threading.Lock()
publish_lock = threading.Lock()


class ApprovalRequest:
    def __init__(self, now):
        self.id = secrets.token_urlsafe(24)
        self.deadline = now + APPROVAL_SECONDS
        self.done = threading.Event()
        self.response = None


class CaptureRequest:
    def __init__(self):
        self.id = str(uuid4())
        self.directory = tempfile.TemporaryDirectory(prefix='macremote-screen-')
        self.path = Path(self.directory.name) / 'screen.jpg'
        self.deadline = time.monotonic() + CAPTURE_SECONDS
        self.done = threading.Event()
        self.image = None
        self.error = None

    def finish(self, image=None, error=None):
        self.image = image
        self.error = error
        try:
            self.directory.cleanup()
        except OSError:
            self.image = None
            self.error = error or 'Mac에서 임시 화면 파일을 정리하지 못했어.'
        finally:
            self.done.set()


class AppCapture:
    def __init__(self, publish):
        self.publish = publish
        self.lock = threading.Lock()
        self.pending = None
        self.stopped = False

    def read(self):
        with self.lock:
            if self.stopped:
                raise RuntimeError(CAPTURE_STOPPED)
            if self.pending is None:
                capture = CaptureRequest()
                try:
                    self.publish({'event': 'capture', 'id': capture.id, 'path': str(capture.path)})
                except OSError:
                    capture.finish(error=CAPTURE_STOPPED)
                    raise RuntimeError(CAPTURE_STOPPED) from None
                self.pending = capture
            capture = self.pending

        if not capture.done.wait(timeout=max(0, capture.deadline - time.monotonic())):
            with self.lock:
                if not capture.done.is_set():
                    # Keep the slot until the launcher acknowledges completion.
                    capture.finish(error=CAPTURE_TIMEOUT)
        if capture.error is not None:
            raise RuntimeError(capture.error)
        return capture.image

    def resolve(self, result):
        if not isinstance(result, dict) or 'error' not in result:
            return
        error = result['error']
        if error is not None and not isinstance(error, str):
            return
        with self.lock:
            capture = self.pending
            if capture is None or result.get('capture') != capture.id:
                return
            self.pending = None
            if capture.done.is_set():
                return
            if time.monotonic() >= capture.deadline:
                capture.finish(error=CAPTURE_TIMEOUT)
            elif error is not None:
                capture.finish(error=error.strip()[:800] or 'Mac 화면을 가져오지 못했어.')
            else:
                try:
                    image = capture.path.read_bytes()
                except OSError:
                    image = None
                capture.finish(image=image, error=None if image else 'Mac 화면 파일을 읽지 못했어.')

    def stop(self):
        with self.lock:
            self.stopped = True
            capture, self.pending = self.pending, None
            if capture is not None and not capture.done.is_set():
                capture.finish(error=CAPTURE_STOPPED)


class InputRequest:
    def __init__(self):
        self.id = str(uuid4())
        self.deadline = time.monotonic() + INPUT_SECONDS
        self.expires_at = time.time() + INPUT_SECONDS
        self.done = threading.Event()
        self.error = None

    def finish(self, error):
        self.error = error
        self.done.set()


class AppInput:
    def __init__(self, publish):
        self.publish = publish
        self.lock = threading.Lock()
        self.pending = None
        self.stopped = False

    def perform(self, args):
        with self.lock:
            if self.stopped:
                raise RuntimeError(INPUT_STOPPED)
            if self.pending is not None:
                raise RuntimeError('Mac에서 다른 입력을 처리 중이야.')
            operation = InputRequest()
            try:
                self.publish({'event': 'input', 'id': operation.id, 'args': args, 'expiresAt': operation.expires_at})
            except OSError:
                raise RuntimeError(INPUT_STOPPED) from None
            self.pending = operation

        if not operation.done.wait(timeout=max(0, operation.deadline - time.monotonic())):
            with self.lock:
                if not operation.done.is_set():
                    # The launcher may have performed the input. Never dispatch it twice.
                    operation.finish(INPUT_TIMEOUT)
        if operation.error is not None:
            raise RuntimeError(operation.error)

    def resolve(self, result):
        if not isinstance(result, dict) or 'error' not in result:
            return
        error = result['error']
        if error is not None and not isinstance(error, str):
            return
        with self.lock:
            operation = self.pending
            if operation is None or result.get('input') != operation.id:
                return
            self.pending = None
            if operation.done.is_set():
                return
            if time.monotonic() >= operation.deadline:
                operation.finish(INPUT_TIMEOUT)
            else:
                if error is not None:
                    error = error.strip()[:800] or 'Mac에 입력을 보내지 못했어.'
                operation.finish(error)

    def stop(self):
        with self.lock:
            self.stopped = True
            operation, self.pending = self.pending, None
            if operation is not None and not operation.done.is_set():
                operation.finish(INPUT_STOPPED)


class RemoteState:
    def __init__(self, publish, internet=False):
        self.publish = publish
        self.internet = internet
        self.lock = threading.Lock()
        self.code = None
        self.deadline = 0
        self.attempts = 0
        self.sessions = {}
        self.pending_approval = None

    def issue_code(self, event='code', **details):
        with self.lock:
            self._finish_approval('renewed')
            previous = self.code
            while self.code == previous:
                self.code = f'{secrets.randbelow(10000):04d}'
            self.deadline = time.monotonic() + PAIR_SECONDS
            self.attempts = PAIR_ATTEMPTS
            self.publish({'event': event, 'code': self.code, **details})

    def pair(self, candidate):
        if not isinstance(candidate, str) or len(candidate) != 4 or not candidate.isascii() or not candidate.isdigit():
            return 400, {'error': '숫자 4자리를 입력해.'}, ()

        with self.lock:
            now = time.monotonic()
            if now >= self.deadline:
                return 410, {'error': '이 코드는 쓸 수 없어. Mac에서 새 코드를 만들어.'}, ()
            if self.attempts == 0:
                return 429, {'error': '입력 횟수를 다 썼어. Mac에서 새 코드를 만들어.'}, ()
            if not hmac.compare_digest(candidate, self.code):
                self.attempts -= 1
                if self.attempts > 0:
                    return 401, {'error': '연결 코드가 틀렸어.'}, ()
                self.publish({'event': 'exhausted'})
                return 429, {'error': '입력 횟수를 다 썼어. Mac에서 새 코드를 만들어.'}, ()
            self.deadline = 0
            if not self.internet:
                return self._pair_success(now)
            approval = ApprovalRequest(now)
            self.pending_approval = approval
            self.publish({'event': 'approval', 'id': approval.id})

        approval.done.wait(timeout=max(0, approval.deadline - time.monotonic()))
        with self.lock:
            if self.pending_approval is approval:
                self._finish_approval('expired')
            return approval.response

    def _pair_success(self, now, **details):
        token = secrets.token_urlsafe(32)
        duration = INTERNET_SESSION_SECONDS if self.internet else SESSION_SECONDS
        self.sessions[token] = now + duration
        self.publish({'event': 'paired', **details})
        headers = [('Set-Cookie', f'mac_remote={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age={duration}')]
        return 200, {'ok': True}, headers

    def _finish_approval(self, reason):
        approval = self.pending_approval
        if approval is None:
            return
        self.pending_approval = None
        if reason == 'approved':
            approval.response = self._pair_success(time.monotonic(), id=approval.id)
            approval.done.set()
            return
        status, description = {
            'denied': (403, 'Mac에서 연결을 거절했어.'),
            'expired': (408, 'Mac에서 확인할 시간이 지났어.'),
            'renewed': (403, 'Mac에서 새 코드를 만들었어.'),
            'shutdown': (403, 'Mac에서 연결을 종료했어.'),
        }[reason]
        approval.response = status, {'error': f'{description} 새 코드로 다시 연결해.'}, ()
        approval.done.set()
        self.publish({'event': 'approval-ended', 'id': approval.id, 'reason': reason})

    def resolve_approval(self, approval_id, approved):
        with self.lock:
            approval = self.pending_approval
            if approval is not None and approval.id == approval_id:
                if time.monotonic() >= approval.deadline:
                    self._finish_approval('expired')
                else:
                    self._finish_approval('approved' if approved else 'denied')

    def stop(self):
        with self.lock:
            self.deadline = 0
            self._finish_approval('shutdown')

    def authenticated(self, token):
        with self.lock:
            now = time.monotonic()
            for key, expiry in list(self.sessions.items()):
                if expiry <= now:
                    del self.sessions[key]
            return token in self.sessions

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)


class RemoteHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, context, state, app_capture=None, app_input=None):
        self.context = context
        self.state = state
        self.app_capture = app_capture
        self.app_input = app_input
        self.public_host = None
        self.request_slots = threading.BoundedSemaphore(24) if state.internet else None
        super().__init__(address, Handler)

    def stop_operations(self):
        if self.app_capture is not None:
            self.app_capture.stop()
        if self.app_input is not None:
            self.app_input.stop()
        self.state.stop()

    def process_request(self, request, client_address):
        if self.request_slots is not None and not self.request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            if self.request_slots is not None:
                self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.request_slots is not None:
                self.request_slots.release()

    def finish_request(self, request, client_address):
        # TLS negotiation happens on the request's worker thread.
        request.settimeout(10)
        try:
            secured = self.context.wrap_socket(request, server_side=True)
        except OSError:
            return
        try:
            super().finish_request(secured, client_address)
        finally:
            self.shutdown_request(secured)


def valid_action(data):
    kind = data.get('type')
    if kind == 'click':
        x, y = data.get('x'), data.get('y')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 30000 for v in (x, y)):
            raise ValueError('좌표가 올바르지 않아.')
        return ['click', str(round(x)), str(round(y))]
    if kind == 'scroll':
        dy = data.get('dy')
        if isinstance(dy, bool) or not isinstance(dy, (int, float)) or not -1000 <= dy <= 1000:
            raise ValueError('스크롤 값이 올바르지 않아.')
        return ['scroll', str(round(dy))]
    if kind == 'key':
        key = data.get('key')
        keys = {'enter', 'tab', 'escape', 'backspace', 'delete', 'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', 'space'}
        if not isinstance(key, str) or (key not in keys and not (len(key) == 1 and key.isascii() and key.isalnum())):
            raise ValueError('지원하지 않는 키야.')
        return ['key', key]
    if kind == 'type':
        value = data.get('text')
        if not isinstance(value, str) or not value or len(value) > 2000 or '\x00' in value:
            raise ValueError('입력할 글자를 확인해줘.')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('입력할 글자를 확인해줘.') from None
        return ['type', value]
    raise ValueError('지원하지 않는 동작이야.')


def run_helper(args, timeout=15):
    with helper_lock:
        proc = subprocess.run([str(HELPER), *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or 'Mac 제어에 실패했어.').strip()[:800])


def capture_with_helper():
    with tempfile.TemporaryDirectory(prefix='macremote-screen-') as tmp:
        image_path = Path(tmp) / 'screen.jpg'
        run_helper(['screenshot', str(image_path)])
        return image_path.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = 'MacRemote/1.0'

    def log_message(self, fmt, *args):
        # Never log access codes, session cookies, or messages.
        pass

    def send_bytes(self, status, body, content_type, headers=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if self.server.state.internet:
            self.send_header('X-Robots-Tag', 'noindex, nofollow')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, status, data, headers=()):
        self.send_bytes(status, json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', headers)

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            return cookie['mac_remote'].value
        except (KeyError, CookieError):
            return None

    def authenticated(self):
        return self.server.state.authenticated(self.session_token())

    def valid_host(self):
        if not self.server.state.internet:
            return True
        return self.headers.get('Host', '').lower() == self.server.public_host

    def valid_origin(self):
        origin = self.headers.get('Origin', '')
        host = self.headers.get('Host', '')
        try:
            parsed = urlsplit(origin)
            return parsed.scheme == 'https' and parsed.netloc.lower() == host.lower()
        except ValueError:
            return False

    def read_json(self):
        if not self.headers.get('Content-Type', '').lower().startswith('application/json'):
            raise ValueError('JSON 요청이 필요해.')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('요청 크기가 올바르지 않아.')
        if length <= 0 or length > MAX_BODY:
            raise ValueError('요청 크기가 올바르지 않아.')
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError('요청 형식이 올바르지 않아.')
        return result

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Mac 앱에 표시된 인터넷 주소로 접속해.'})
        path = urlsplit(self.path).path
        if path == '/api/status':
            return self.send_json(200, {'authenticated': self.authenticated(), 'internet': self.server.state.internet})
        if path == '/api/screen':
            if not self.authenticated():
                return self.send_json(401, {'error': '먼저 연결 코드를 입력해줘.'})
            try:
                capture = self.server.app_capture
                image = capture.read() if capture is not None else capture_with_helper()
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                return self.send_json(503, {'error': str(exc)[:800]})
            if not self.authenticated():
                return self.send_json(401, {'error': '먼저 연결 코드를 입력해줘.'})
            return self.send_bytes(200, image, 'image/jpeg')
        files = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/manifest.webmanifest': 'manifest.webmanifest', '/service-worker.js': 'service-worker.js', '/icon-192.png': 'icon-192.png', '/icon-512.png': 'icon-512.png', '/apple-touch-icon.png': 'apple-touch-icon.png'}
        if path in files:
            try:
                body = (PUBLIC / files[path]).read_bytes()
                kind = {'manifest.webmanifest': 'application/manifest+json', 'service-worker.js': 'application/javascript'}.get(files[path]) or mimetypes.guess_type(files[path])[0] or 'application/octet-stream'
                if kind.startswith('text/') or kind in ('application/javascript', 'application/manifest+json'):
                    kind += '; charset=utf-8'
                return self.send_bytes(200, body, kind)
            except OSError:
                pass
        return self.send_json(404, {'error': '찾을 수 없어.'})

    def do_POST(self):
        if not self.valid_host() or not self.valid_origin():
            return self.send_json(403, {'error': '요청 출처를 확인할 수 없어.'})
        path = urlsplit(self.path).path
        try:
            data = self.read_json()
        except (ValueError, UnicodeError, TimeoutError):
            return self.send_json(400, {'error': '요청 형식이 올바르지 않아.'})
        if path == '/api/login':
            return self.send_json(*self.server.state.pair(data.get('code')))
        if not self.authenticated():
            return self.send_json(401, {'error': '먼저 연결 코드를 입력해줘.'})
        if path == '/api/logout':
            self.server.state.logout(self.session_token())
            return self.send_json(200, {'ok': True}, [('Set-Cookie', 'mac_remote=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0')])
        if path == '/api/action':
            try:
                args = valid_action(data)
                if self.server.app_input is not None:
                    self.server.app_input.perform(args)
                else:
                    run_helper(args)
                return self.send_json(200, {'ok': True})
            except ValueError as exc:
                return self.send_json(400, {'error': str(exc)})
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                return self.send_json(503, {'error': str(exc)[:800]})
        return self.send_json(404, {'error': '찾을 수 없어.'})


def read_commands(server, stopped):
    try:
        for line in sys.stdin:
            line = line.strip()
            if line.startswith('{'):
                try:
                    result = json.loads(line)
                except ValueError:
                    continue
                if isinstance(result, dict):
                    if 'capture' in result and server.app_capture is not None:
                        server.app_capture.resolve(result)
                    elif 'input' in result and server.app_input is not None:
                        server.app_input.resolve(result)
                continue
            parts = line.split()
            if parts == ['pair']:
                server.state.issue_code()
            elif parts == ['quit']:
                break
            elif len(parts) == 2 and parts[0] in ('approve', 'deny'):
                server.state.resolve_approval(parts[1], approved=parts[0] == 'approve')
    finally:
        stopped.set()
        try:
            server.stop_operations()
        finally:
            server.shutdown()


def publish(event):
    with publish_lock:
        print(json.dumps(event, ensure_ascii=True), flush=True)


def serve_internet(server, state, certificates, stopped, ready):
    with quick_tunnel(ready['port'], certificates.ca_pem, stopped) as (address, tunnel):
        if stopped.is_set():
            return
        server.public_host = urlsplit(address).hostname

        def watch_tunnel():
            tunnel.wait()
            server.shutdown()

        state.issue_code('ready', urls=[address], **ready)
        threading.Thread(target=watch_tunnel, daemon=True).start()
        server.serve_forever(poll_interval=0.2)
        if not stopped.is_set():
            raise RuntimeError('인터넷 연결이 끊겼어. 다시 시작을 눌러.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--host')
    parser.add_argument('--internet', action='store_true')
    parser.add_argument('--app-capture', action='store_true')
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    if args.internet and args.host not in (None, '127.0.0.1'):
        parser.error('인터넷 연결 모드의 서버는 이 Mac 안에서만 열 수 있어.')
    host = '127.0.0.1' if args.internet else args.host or '0.0.0.0'
    if not args.app_capture and not HELPER.is_file():
        raise SystemExit('Mac 제어 도구를 찾을 수 없어. 앱을 다시 빌드해.')
    os.umask(0o077)
    ips = local_addresses()
    certificates = CertificateStore(args.runtime.resolve())
    context = certificates.prepare(ips)
    profile = certificates.export_safari_profile()
    state = RemoteState(publish, internet=args.internet)
    app_capture = AppCapture(publish) if args.app_capture else None
    app_input = AppInput(publish) if args.app_capture else None
    with RemoteHTTPServer((host, args.port), context, state, app_capture=app_capture, app_input=app_input) as server:
        bound_host, port = server.server_address
        ready = {'port': port, 'certificate': str(certificates.public_cert), 'profile': str(profile)}
        stopped = threading.Event()
        if not args.internet:
            urls = [f'https://{ip}:{port}' for ip in ips if bound_host in ('0.0.0.0', ip)]
            state.issue_code('ready', urls=urls, **ready)
        threading.Thread(target=read_commands, args=(server, stopped), daemon=True).start()
        try:
            if args.internet:
                serve_internet(server, state, certificates, stopped, ready)
            else:
                server.serve_forever(poll_interval=0.2)
        finally:
            server.stop_operations()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError, ValueError) as exc:
        sys.exit(str(exc))
