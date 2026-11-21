#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"

staging="$(mktemp -d "${TMPDIR:-/tmp}/mac-remote-build.XXXXXXXX")"
trap 'rm -rf -- "$staging"' EXIT
app="$staging/Mac 리모컨.app"
contents="$app/Contents"
output="$HOME/Applications/Mac 리모컨.app"
target="$(uname -m)-apple-macos14.0"

mkdir -p "$contents/MacOS" "$contents/Resources/native"
cp -X native/Info.plist "$contents/Info.plist"
cp -X server.py certificates.py internet_tunnel.py fixed_entry.py screen_stream.py "$contents/Resources/"
cp -RX public "$contents/Resources/"
/usr/bin/swiftc -parse-as-library -target "$target" native/Launcher.swift native/ScreenCapture.swift native/RemoteInput.swift -o "$contents/MacOS/MacRemote"
/usr/bin/swiftc -parse-as-library -target "$target" native/RemoteHelper.swift native/ScreenCapture.swift native/RemoteInput.swift -o "$contents/Resources/native/remote-helper"

iconset="$staging/AppIcon.iconset"
mkdir "$iconset"
for size in 16 32 128 256 512; do
  /usr/bin/sips -z "$size" "$size" public/icon-512.png --out "$iconset/icon_${size}x${size}.png" >/dev/null
  retina=$((size * 2))
  /usr/bin/sips -z "$retina" "$retina" public/icon-512.png --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$iconset" -o "$contents/Resources/AppIcon.icns"

/usr/bin/xattr -cr "$app"
/usr/bin/codesign --force --sign - --identifier local.macremote.pairing.helper "$contents/Resources/native/remote-helper"
/usr/bin/codesign --force --sign - "$app"
/usr/bin/codesign --verify --strict "$app"

mkdir -p "${output:h}"
if [[ -e "$output" ]]; then
  existing_id="$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$output/Contents/Info.plist")"
  if [[ "$existing_id" != 'local.macremote.pairing' ]]; then
    print -u2 '같은 이름의 다른 앱이 설치되어 있습니다.'
    exit 1
  fi
  mv "$output" "$staging/previous.app"
fi
if ! mv "$app" "$output" || ! /usr/bin/codesign --verify --strict "$output"; then
  [[ ! -e "$output" ]] || mv "$output" "$staging/failed.app"
  [[ ! -e "$staging/previous.app" ]] || mv "$staging/previous.app" "$output"
  exit 1
fi
print -r -- "$output"
