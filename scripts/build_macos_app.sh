#!/usr/bin/env bash
set -euo pipefail

# Build a native Music Suite .app and .dmg on macOS (Apple Silicon and Intel).
# The app runs the bundled API + Next.js server and displays the UI in a
# WKWebView window. Homebrew and Xcode Command Line Tools are required.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script must be run on macOS." >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -x /usr/local/bin/brew ]] && eval "$(/usr/local/bin/brew shellenv)"
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc is missing. Install Xcode Command Line Tools: xcode-select --install" >&2
  exit 1
fi

echo "Music Suite macOS app builder ($(uname -m), $(sw_vers -productVersion))"

# The app bundle carries its own Python environment and web build.
if [[ ! -x .venv/bin/python || ! -d apps/web-next/node_modules || ! -f apps/web-next/.next/BUILD_ID ]]; then
  echo "Dependencies are missing; running install.command first..."
  MUSIC_SUITE_INSTALL_AI=0 "$ROOT/install.command"
fi

DIST="$ROOT/dist"
APP="$DIST/Music Suite.app"
DMG="$DIST/MusicSuite.dmg"
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/music-suite-app.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT

rm -rf "$APP" "$DMG"
mkdir -p "$DIST" "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "Compiling native launcher..."
# The release DMG is Apple Silicon only; build for arm64 explicitly so an Intel
# host cannot silently produce a binary this project does not ship.
if [[ "$(uname -m)" == "arm64" ]]; then
  swiftc -O -target arm64-apple-macos12.0 "$ROOT/macos/main.swift" \
    -o "$APP/Contents/MacOS/MusicSuiteLauncher" \
    -framework AppKit -framework WebKit
else
  echo "Note: building on $(uname -m); the released DMG is Apple Silicon only." >&2
  swiftc -O "$ROOT/macos/main.swift" \
    -o "$APP/Contents/MacOS/MusicSuiteLauncher" \
    -framework AppKit -framework WebKit
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>MusicSuiteLauncher</string>
  <key>CFBundleIdentifier</key><string>com.ltillegal.music-suite</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Music Suite</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleIconFile</key><string>AppIcon.icns</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsArbitraryLoads</key><false/>
    <key>NSExceptionDomains</key>
    <dict>
      <key>127.0.0.1</key>
      <dict><key>NSExceptionAllowsInsecureHTTPLoads</key><true/></dict>
      <key>localhost</key>
      <dict><key>NSExceptionAllowsInsecureHTTPLoads</key><true/></dict>
    </dict>
  </dict>
</dict>
</plist>
PLIST

echo "Building icon..."
python3 "$ROOT/macos/make_icon.py" "$BUILD/AppIcon.png" >/dev/null
ICONSET="$BUILD/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z "$size" "$size" "$BUILD/AppIcon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$BUILD/AppIcon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

echo "Bundling the suite (this includes .venv and node_modules and can take a while)..."
SUITE="$APP/Contents/Resources/suite"
mkdir -p "$SUITE"
rsync -a \
  --exclude '/.git' --exclude '/dist' --exclude '/logs' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude '.music-suite-processes.json' --exclude '.music-suite-install-state' \
  --exclude '/data' --exclude '/models' --exclude '/dist' \
  "$ROOT/" "$SUITE/"

# The bundle has no .git directory, so match start.command's fallback revision.
python3 -c 'import hashlib, pathlib; files=(pathlib.Path("pyproject.toml"), pathlib.Path("apps/web-next/pnpm-lock.yaml")); print("-".join(hashlib.sha256(p.read_bytes()).hexdigest().upper() for p in files)+"-no-git-revision")' > "$SUITE/.music-suite-install-state"

codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "Note: ad-hoc signing was skipped."

echo "Creating DMG..."
hdiutil create -volname "Music Suite" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null

du -sh "$APP" "$DMG"
echo "Done:"
echo "  $APP"
echo "  $DMG"
