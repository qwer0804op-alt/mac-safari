# Mac 리모컨

Safari에서 Mac(또는 Windows PC)의 주 화면을 보고, 탭으로 클릭하고, 스크롤과 텍스트를 보낼 수 있어. **인터넷 연결**을 켜면 다른 Wi-Fi나 셀룰러에서도 접속할 수 있어. Mac에는 macOS 14 이상과 Apple Command Line Tools가 필요해.

## 맥에서 시작하기

1. Finder에서 사용자 응용 프로그램 폴더의 **Mac 리모컨.app**을 열어. 소스에서 설치하려면 터미널에서 이 폴더로 이동해 `zsh build.command`를 실행해.
2. 처음 사용할 때는 앱의 **화면 보기 권한**, **클릭·입력 권한** 버튼을 눌러. 시스템 설정의 **개인정보 보호 및 보안**에서 **화면 및 시스템 오디오 녹화**와 **손쉬운 사용**에 Mac 리모컨을 허용해. macOS에서 앱을 재시작하라고 하면 닫았다 다시 열어.
3. 앱에 표시된 **Safari 접속 주소**와 **4자리 페어링 코드**를 확인해. 코드는 발급 후 5분 안에 한 번 쓸 수 있어. 만료됐거나 5번 틀렸으면 **새 코드**를 눌러.

연결됐는데 화면이 비어 있으면 맥 앱에서 **화면 확인**을 눌러. 성공하면 이 Mac이 화면을 캡처했다는 메시지가 나와. 권한 안내가 나오면 **화면 보기 권한**에서 현재 설치한 Mac 리모컨의 화면 기록을 허용해. 앱을 다시 열었다면 맥에 새로 표시된 주소와 코드로 접속해.

## 컴퓨터 브라우저에서 조작하기

휴대폰이 아니라 다른 컴퓨터의 Chrome, Edge, Safari 같은 브라우저에서도 같은 주소와 4자리 코드로 연결할 수 있습니다.

- **클릭**: 화면을 마우스로 누르면 해당 위치를 클릭합니다.
- **스크롤**: 화면 위에서 마우스 휠을 돌리면 스크롤합니다. 빠르게 돌려도 묶어서 보내 Mac이나 PC가 밀리지 않습니다.
- **키보드 입력**: 화면을 한 번 누르면 키보드가 연결됩니다. 글자와 한글이 그대로 입력되고, Enter, Tab, Esc, Backspace, Delete, 방향키, Home, End, PageUp, PageDown은 키로 전달됩니다. 붙여넣기(Ctrl+V 또는 ⌘V)한 글자도 보냅니다.
- Ctrl, ⌘, Alt를 누른 단축키는 전달하지 않고 브라우저가 처리합니다.
- 휴대폰에서 탭할 때는 화상 키보드가 열리지 않습니다. 휴대폰에서는 아래의 **텍스트 입력**을 사용해 주세요.

## 인터넷에서 연결하기

1. Mac에 [cloudflared](https://developers.cloudflare.com/tunnel/downloads/)를 설치해. Homebrew를 쓰면 `brew install cloudflared`로 설치할 수 있어.
2. Mac 리모컨에서 **인터넷 연결**을 켜. 화면에 표시된 공개 HTTPS 주소를 Safari에서 열고 **숫자 네 자리**를 입력해. Safari에 따로 인증서를 설치할 필요는 없어.
3. 접속을 기다리던 요청이면 Mac에서 **허용**을 눌러. 브라우저와 Mac 사이의 인터넷 연결은 Cloudflare가 중계하고, 브라우저의 연결은 최대 1시간 동안 유지돼.

Mac 앱이나 인터넷 연결을 종료하면 공개 주소는 닫혀. 다시 켜면 새 주소가 발급되고, Mac은 켜져 있어야 해. [Cloudflare Quick Tunnel 안내](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## 같은 Wi-Fi에서 연결하기

1. 첫 연결 전에 Mac 앱에서 **Safari 보안 설정 → 아이폰 프로파일 보기**를 눌러. Finder에 표시되는 **Mac-Remote-Safari.mobileconfig**를 아이폰에 AirDrop해.
2. 아이폰에서 **설정 → 일반 → VPN 및 기기 관리**로 가서 받은 프로파일을 설치해. **설정 → 일반 → 정보 → 인증서 신뢰 설정**에서 **Mac Remote Pairing CA**의 전체 신뢰를 켜. 이 프로파일에는 이 앱이 쓰는 공개 루트 인증서만 들어 있어. 설치하는 기기에서 직접 신뢰를 켜야 하고, 이미 같은 인증서를 신뢰했다면 다시 설치할 필요가 없어. [Apple 인증서 신뢰 안내](https://support.apple.com/ko-kr/102390)
3. 같은 Wi-Fi에 연결한 아이폰의 Safari에서 Mac 앱에 뜬 `https://…:8766` 주소를 열어. Mac 앱에 보이는 **숫자 네 자리**를 그대로 입력해.
4. 홈 화면에서 바로 열고 싶으면 Safari에서 **공유 → 홈 화면에 추가 → 웹 앱으로 열기**를 선택해. 홈 화면 웹 앱에서 처음 열 때는 Mac에서 새 코드를 받아야 할 수 있어. [Apple 홈 화면 웹 앱 안내](https://support.apple.com/ko-kr/guide/iphone/iphea86e5236/ios)

Mac 앱 창을 닫으면 서버와 기존 연결이 종료돼. Mac의 Wi-Fi 주소가 바뀌면 앱을 다시 열고 새 주소로 접속해. 이 앱을 더 이상 쓰지 않으면 아이폰의 **VPN 및 기기 관리**에서 설치한 인증서 프로파일을 제거해.

## Mac Safari에서 연결하기

Mac 앱에서 **Safari 보안 설정 → Mac 인증서 보기**를 눌러. 표시된 공개 인증서를 키체인 접근의 로그인 키체인에 추가해. **Mac Remote Pairing CA**를 열고 **신뢰 → SSL**을 설정한 뒤 앱에 표시된 HTTPS 주소를 열어. [Apple 키체인 인증서 신뢰 설정 안내](https://support.apple.com/ko-kr/guide/keychain-access/kyca11871/mac)

## Windows PC에서 사용하기

Windows 10 이상, [Python 3](https://www.python.org/downloads/)(설치할 때 **Add python.exe to PATH** 선택), OpenSSL이 필요합니다. [Git for Windows](https://git-scm.com/download/win)에 OpenSSL이 들어 있어 따로 설치하지 않아도 됩니다. 다른 OpenSSL을 쓰려면 환경 변수 `MACREMOTE_OPENSSL`에 `openssl.exe` 경로를 지정해 주세요.

1. 이 폴더에서 **start-windows.bat**을 실행해 주세요. 인터넷에서 연결하려면 명령 프롬프트에서 `start-windows.bat --internet`을 실행하고, 먼저 `winget install Cloudflare.cloudflared`로 cloudflared를 설치해 주세요.
2. 창에 표시된 **접속 주소**를 휴대폰 브라우저에서 열고 **4자리 코드**를 입력해 주세요. 같은 Wi-Fi에서 연결할 때는 Windows 방화벽 허용 창이 나오면 **개인 네트워크**를 허용해 주세요.
3. 같은 Wi-Fi에서 처음 연결할 때는 창에 표시된 인증서 파일을 휴대폰에 설치해야 합니다. 아이폰은 표시된 프로파일을 설치한 뒤 **인증서 신뢰 설정**을 켜 주세요. Android는 **설정 → 보안 → 암호화 및 사용자 인증 정보 → 인증서 설치 → CA 인증서**에서 인증서 파일을 선택해 주세요. 인터넷 연결은 인증서 설치가 필요 없습니다.
4. 인터넷 연결에서는 휴대폰에서 코드를 입력하면 Windows 창에 승인 요청이 표시됩니다. 직접 요청한 연결이면 `y`를 입력해 주세요. `c`는 새 코드, `n`은 거절, `q`는 종료입니다.

화면은 주 모니터만 전송합니다. 관리자 권한으로 실행한 프로그램, 잠금 화면, UAC 보안 데스크톱에는 입력할 수 없습니다. `pip install pillow`를 하면 화면 전송이 더 빨라집니다. 이 앱을 종료하면 공개 주소와 연결이 모두 닫힙니다.

## 테스트

`/usr/bin/python3 -B -m unittest discover -s tests -v`로 페어링, 코드 만료 및 재사용 제한, HTTPS 연결, 로그아웃, 서버 종료를 확인할 수 있어.
