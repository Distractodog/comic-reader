#!/bin/bash
# Build/rebuild the local macOS Cover 2.0.app launcher.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${ROOT}/Cover 2.0.app"
ICNS="${ROOT}/src/assets/app-icon.icns"
ENTITLEMENTS="${ROOT}/scripts/entitlements.plist"
LAUNCHER_SRC="${ROOT}/scripts/macos_launcher.c"
APP_PAYLOAD="${APP}/Contents/Resources/app"

if [[ ! -x "${ROOT}/venv/bin/python" ]]; then
  echo "venv not found. Run: python3.12 -m venv venv && pip install -r requirements.txt" >&2
  exit 1
fi

"${ROOT}/venv/bin/python" "${ROOT}/scripts/build_macos_icon.py"

MACOS="${APP}/Contents/MacOS"
RESOURCES="${APP}/Contents/Resources"
mkdir -p "$MACOS" "$APP_PAYLOAD/src" "$APP_PAYLOAD/site-packages"

clang -O2 -Wall -o "${MACOS}/boot" "$LAUNCHER_SRC"

FRAMEWORK_PYTHON="$("${ROOT}/venv/bin/python" - <<'PY'
import pathlib
import sys

base = pathlib.Path(sys.base_prefix)
candidate = base / "Resources/Python.app/Contents/MacOS/Python"
print(candidate if candidate.exists() else pathlib.Path(sys.executable).resolve())
PY
)"
ln -sf "${FRAMEWORK_PYTHON}" "${MACOS}/Cover 2.0"

REQ_HASH="$(shasum -a 256 "${ROOT}/requirements.txt" | awk '{print $1}')"
if [[ ! -f "${APP_PAYLOAD}/.deps-stamp" ]] || [[ "$(cat "${APP_PAYLOAD}/.deps-stamp")" != "$REQ_HASH" ]]; then
  echo "Syncing Python packages into Cover 2.0.app (runs when requirements.txt changes)..."
  rsync -a --delete "${ROOT}/venv/lib/python3.12/site-packages/" "${APP_PAYLOAD}/site-packages/"
  echo "$REQ_HASH" > "${APP_PAYLOAD}/.deps-stamp"
fi

echo "Syncing app source into Cover 2.0.app..."
rsync -a --delete --exclude '__pycache__/' "${ROOT}/src/" "${APP_PAYLOAD}/src/"

if [[ ! -f "${APP}/Contents/Info.plist" ]]; then
  cat > "${APP}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>Cover 2.0</string>
    <key>CFBundleExecutable</key>
    <string>boot</string>
    <key>CFBundleIconFile</key>
    <string>app-icon</string>
    <key>CFBundleIdentifier</key>
    <string>com.comicreader.cover</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Cover 2.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0</string>
    <key>CFBundleVersion</key>
    <string>2.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSEnvironment</key>
    <dict>
        <key>DYLD_LIBRARY_PATH</key>
        <string>/opt/homebrew/opt/expat/lib</string>
    </dict>
</dict>
</plist>
PLIST
  printf 'APPL????' > "${APP}/Contents/PkgInfo"
fi

if [[ -f "$ICNS" ]]; then
  cp "$ICNS" "${RESOURCES}/app-icon.icns"
fi

xattr -cr "$APP" 2>/dev/null || true
find "$APP" -name .DS_Store -delete 2>/dev/null || true
codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$APP" 2>/dev/null || true

echo "Built ${APP}"
echo ""
echo "When launched from this repo folder, Cover 2.0.app runs your LIVE src/ and"
echo "venv directly — edit files in src/ and just relaunch; no re-sync needed."
echo "Re-run this script only after: changing the launcher/icon/Info.plist, or to"
echo "refresh the bundled fallback copy used if the .app is moved out standalone."
echo "First launch: right-click Cover 2.0.app → Open → Open."
echo "Backup launcher: double-click Launch Cover 2.0.command"
