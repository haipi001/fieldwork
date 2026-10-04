#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
APP_NAME="Fieldwork"
BUILD_ROOT="${FIELDWORK_BUILD_OUTPUT:-$PROJECT_ROOT/build/macos}"
APP_BUNDLE="$BUILD_ROOT/$APP_NAME.app"
ICON_SOURCE="$PROJECT_ROOT/static/fieldwork-magnifier-icon.png"
ICONSET="$BUILD_ROOT/$APP_NAME.iconset"

mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" "$ICONSET"
cp "$PROJECT_ROOT/macos/Info.plist" "$APP_BUNDLE/Contents/Info.plist"
python3 - "$PROJECT_ROOT" "$APP_BUNDLE/Contents/Info.plist" <<'PY'
import plistlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from version import APP_VERSION, BUILD_NUMBER

path = Path(sys.argv[2])
with path.open('rb') as stream:
    metadata = plistlib.load(stream)
metadata['CFBundleShortVersionString'] = APP_VERSION
metadata['CFBundleVersion'] = str(BUILD_NUMBER)
launch_root = Path(os.environ.get('FIELDWORK_BUILD_PROJECT_ROOT', sys.argv[1])).resolve()
if not (launch_root / 'desktop_server.py').is_file():
    raise ValueError('launch source root is missing desktop_server.py')
python_path = Path(os.environ.get('FIELDWORK_BUILD_PYTHON', sys.executable)).absolute()
if not python_path.is_file() or not os.access(python_path, os.X_OK):
    raise ValueError('launch Python must be an executable file')
port = int(os.environ.get('FIELDWORK_BUILD_PORT', '8000'))
if not 1 <= port <= 65535:
    raise ValueError('invalid launch port')
metadata['FieldworkProjectRoot'] = str(launch_root)
metadata['FieldworkPythonExecutable'] = str(python_path)
metadata['FieldworkPort'] = port
identifier = os.environ.get('FIELDWORK_BUILD_IDENTIFIER', metadata['CFBundleIdentifier'])
if not re.fullmatch(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)+', identifier):
    raise ValueError('invalid bundle identifier')
metadata['CFBundleIdentifier'] = identifier
with path.open('wb') as stream:
    plistlib.dump(metadata, stream, sort_keys=False)
PY

swiftc "$PROJECT_ROOT/macos/FieldworkApp.swift" \
  -target arm64-apple-macos13.0 \
  -framework Cocoa -framework Security -framework WebKit \
  -o "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

swiftc "$PROJECT_ROOT/macos/FieldworkKeychain.swift" \
  -target arm64-apple-macos13.0 \
  -framework Foundation -framework Security \
  -o "$APP_BUNDLE/Contents/MacOS/FieldworkKeychain"

for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/$APP_NAME.icns"
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null

echo "$APP_BUNDLE"
