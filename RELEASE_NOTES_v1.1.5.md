# Mac 리모컨 v1.1.5

버전은 1.1.5이며 Mac 빌드 번호는 6입니다. 이번 버전은 Mac 본체의 외형과 기존 기능은 그대로 두고, Safari 조작 중 발생하던 작은 버그를 수정했습니다. 별도로 설치하는 Android 앱, iPhone 앱, 고정 HTTPS 연결 도우미를 함께 제공합니다.

## 수정 사항

- 화면 밖에서 손을 떼거나 드래그 후 처음 위치로 돌아왔을 때 발생하던 잘못된 클릭을 수정했습니다.
- 탭을 백그라운드로 보내거나 네트워크가 끊어지면 전송 대기 중인 입력을 취소합니다. 이미 전송한 조작은 자동으로 반복하지 않습니다.
- 첫 연결 상태 조회에 실패해도 3초 간격으로 다시 확인합니다. 네트워크나 화면으로 복귀하면 현재 세션으로 화면 갱신을 재개합니다.
- 앱과 웹의 연결, 권한, 오류 안내를 존댓말로 정리했습니다.

## 보안 정책 (변경 없음)

- 4자리 일회용 페어링 코드(5분 유효, 5회 오입력 시 만료)를 유지합니다.
- 인터넷 연결 시 Mac에서 직접 승인해야 연결됩니다.
- TLS 인증서 및 접속 호스트·출처 검증을 유지합니다.

## 새 구성 요소 (선택 설치)

- **Android 앱** — Android 7.0 이상용 앱입니다. 등록한 고정 HTTPS 주소를 브라우저 맞춤 탭으로 엽니다.
- **iPhone 앱** — iOS 16 이상용 Xcode 프로젝트 소스입니다. 고정 주소를 Safari 보기로 엽니다.
- **고정 HTTPS 연결 도우미** — 인터넷 모드를 다시 켜서 Cloudflare 주소가 바뀌어도 휴대폰은 같은 주소로 접속할 수 있습니다. 사이트는 Mac의 기기 서명을 확인한 뒤 현재 Mac 주소로 이동시키며, 화면·클릭·코드·세션 쿠키는 사이트를 거치지 않습니다.

## 다운로드

| 파일 | 설명 |
| --- | --- |
| `mac-safari-1.1.5-source.zip` | Mac 앱 전체 소스입니다. 압축을 풀고 `zsh build.command`로 빌드해 주세요. |
| `mac-safari-1.1.5.patch` | v1.1.2 소스에 적용하는 패치입니다. |
| `MacRemote-1.1.5-debug.apk` | Android 디버그 빌드입니다. Google Play 배포용 서명은 하지 않았습니다. |
| `MacRemote-Android-1.1.5-source.zip` | Android 앱 소스입니다. |
| `MacRemote-iOS-1.1.5-source.zip` | iPhone 앱 소스입니다. 실제 기기에는 Xcode에서 본인 개발 팀으로 서명해 설치해 주세요. |
| `MacRemote-fixed-entry-1.1.5.zip` | 고정 HTTPS 연결 도우미와 사이트 소스입니다. |

### SHA-256

```
1d8bbc764c28a825ab216a9bc2418a7271ec85e058b50bd6130667893f3c135a  mac-safari-1.1.5-source.zip
61c156420990d67ef587daea30e9d7f9c829ecad289eeb68beee72ec332c750d  mac-safari-1.1.5.patch
984ebe5b7ad8a46fdac8908e6ea28c09858d5355beb9c52ace0525c74590420a  MacRemote-1.1.5-debug.apk
6734d31b66403892a2558bbdafc8ef66d16d1ef2dce984e4d3e57e3cac0ea3eb  MacRemote-Android-1.1.5-source.zip
513751fbc0a82f3c0d3d2c4d7c0a4047fc52c023646d3395ab61d2c99a96d6ab  MacRemote-iOS-1.1.5-source.zip
9d20dbdea6cca04cbbecfafe2228755c3a73d68835ccaa881310afcb04af26ed  MacRemote-fixed-entry-1.1.5.zip
```

## 검증 범위

- 클라우드 Linux에서 HTTPS 서버, 페어링, 입력·캡처 시간 제한 테스트와 모바일 Chromium 회귀 테스트 9개가 모두 통과했습니다.
- Android APK의 서명(v2/v3)과 실행 화면 선언을 확인했습니다.

## 알려진 제한

- 이 버전의 macOS 앱 설치본(.app)과 iPhone IPA는 제공하지 않습니다. 소스로 직접 빌드해 주세요.
- macOS 빌드, 실제 Mac의 화면 캡처·클릭, iPhone Safari 및 Android 실기기 동작은 아직 검증하지 못했습니다.
- 인터넷 연결에는 Mac에 `cloudflared`가 필요합니다.
