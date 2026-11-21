#!/usr/bin/env python3
"""Console launcher for Mac Remote on Windows. Run it with start-windows.bat."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

import fixed_entry

ROOT = Path(__file__).resolve().parent
HELP = '명령: c = 새 코드, y = 연결 허용, n = 연결 거절, q = 종료'


class Console:
    def __init__(self, internet):
        self.internet = internet
        self.approval = None

    def handle(self, event):
        """Return the lines to show for one server event."""
        kind = event.get('event')
        if kind == 'ready':
            urls = event.get('urls') or []
            lines = ['접속 주소: ' + (urls[0] if urls else 'Wi-Fi 연결을 확인해 주세요.')]
            if event.get('code'):
                lines.append('4자리 코드: ' + str(event['code']))
            if self.internet:
                lines.append('휴대폰 브라우저에서 주소를 열고 코드를 입력하면 이 창에 승인 요청이 표시됩니다.')
            else:
                lines.append('같은 Wi-Fi의 기기 브라우저에서 주소를 열고 코드를 입력해 주세요. 처음 한 번은 인증서를 설치해야 합니다.')
                if event.get('certificate'):
                    lines.append('인증서 파일: ' + str(event['certificate']))
                if event.get('profile'):
                    lines.append('아이폰용 프로파일: ' + str(event['profile']))
            lines.append(HELP)
            return lines
        if kind == 'register':
            lines = ['고정 주소에 이 PC를 등록하는 중입니다. 브라우저가 열리면 안내에 따라 주세요.',
                     '브라우저가 열리지 않으면 아래 주소를 5분 안에 직접 열어 주세요.', str(event.get('url', ''))]
            if event.get('fingerprint'):
                lines.append('처음 등록할 때는 사이트에 기기 지문 ' + str(event['fingerprint']) + '을 입력해 주세요.')
            return lines
        if kind == 'register-failed':
            return ['고정 주소 등록 창을 열지 못했습니다. c를 입력해 새 코드를 받거나 다시 시작해 주세요.']
        if kind == 'code':
            self.approval = None
            return ['새 4자리 코드: ' + str(event.get('code', ''))]
        if kind == 'paired':
            self.approval = None
            return ['기기를 연결했습니다. 다시 연결하려면 c를 입력해 새 코드를 받아 주세요.']
        if kind == 'exhausted':
            return ['코드를 다섯 번 틀렸습니다. c를 입력해 새 코드를 받아 주세요.']
        if kind == 'approval':
            self.approval = event.get('id')
            return ['연결 요청이 왔습니다. 내가 요청한 연결이면 y, 아니면 n을 입력해 주세요. (50초 안에)']
        if kind == 'approval-ended':
            if event.get('id') == self.approval:
                self.approval = None
            reason = '거절했습니다' if event.get('reason') == 'denied' else '승인 대기가 끝났습니다'
            return [reason + '. c를 입력해 새 코드를 받아 주세요.']
        return []

    def command(self, text):
        """Return (server_command, message) for something the user typed."""
        text = text.strip().lower()
        if text == 'c':
            return 'pair', None
        if text == 'q':
            return 'quit', None
        if text in ('y', 'n'):
            if self.approval is None:
                return None, '지금 승인할 연결 요청이 없습니다.'
            command = ('approve ' if text == 'y' else 'deny ') + self.approval
            self.approval = None
            return command, None
        return None, HELP if text else None


def pump(stream, console, out):
    for line in stream:
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            for message in console.handle(event):
                print(message, file=out, flush=True)


def runtime_directory():
    return fixed_entry.default_runtime()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--internet', action='store_true')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--fixed-entry', metavar='주소', help='고정 HTTPS 사이트 주소를 저장하고 인터넷 연결로 시작합니다. clear를 쓰면 설정을 지웁니다.')
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    runtime = runtime_directory()
    runtime.mkdir(parents=True, exist_ok=True)
    if args.fixed_entry == 'clear':
        fixed_entry.clear_origin(runtime)
        print('고정 주소 설정을 지웠습니다.')
        return 0
    if args.fixed_entry:
        try:
            origin = fixed_entry.save_origin(runtime, args.fixed_entry)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        args.internet = True
        print('고정 주소를 저장했습니다:', origin)
    elif args.internet:
        try:
            saved = fixed_entry.load_origin(runtime)
        except ValueError:
            saved = None
        if saved:
            print('저장된 고정 주소를 사용합니다:', saved, '(설정을 지우려면 --fixed-entry clear)')
    command = [sys.executable, '-B', str(ROOT / 'server.py'), '--runtime', str(runtime), '--port', str(args.port)]
    if args.internet:
        command.append('--internet')
    print('Mac 리모컨 (Windows) 서버를 시작하는 중입니다…', flush=True)
    console = Console(args.internet)
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding='utf-8', errors='replace') as server:
        errors = []
        threading.Thread(target=pump, args=(server.stdout, console, sys.stdout), daemon=True).start()
        threading.Thread(target=lambda: errors.extend(server.stderr), daemon=True).start()

        def forward():
            try:
                for line in sys.stdin:
                    message_command, message = console.command(line)
                    if message:
                        print(message, flush=True)
                    if message_command:
                        server.stdin.write(message_command + '\n')
                        server.stdin.flush()
                        if message_command == 'quit':
                            return
            except (OSError, ValueError):
                pass

        threading.Thread(target=forward, daemon=True).start()
        try:
            code = server.wait()
        except KeyboardInterrupt:
            code = 0
            try:
                server.stdin.write('quit\n')
                server.stdin.flush()
            except OSError:
                pass
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        if code and errors:
            print(''.join(errors).strip()[-800:], file=sys.stderr)
        return code


if __name__ == '__main__':
    sys.exit(main())
