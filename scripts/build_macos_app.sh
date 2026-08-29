#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="${0:A:h:h}"
APP_NAME="Fieldwork"
BUILD_ROOT="$PROJECT_ROOT/build/macos"
APP_BUNDLE="$BUILD_ROOT/$APP_NAME.app"
ICON_SOURCE="$PROJECT_ROOT/static/fieldwork-magnifier-icon.png"
ICONSET="$BUILD_ROOT/$APP_NAME.iconset"

mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources" "$ICONSET"
cp "$PROJECT_ROOT/macos/Info.plist" "$APP_BUNDLE/Contents/Info.plist"

swiftc "$PROJECT_ROOT/macos/FieldworkApp.swift" \
  -target arm64-apple-macos13.0 \
  -framework Cocoa -framework WebKit \
  -o "$APP_BUNDLE/Contents/MacOS/$APP_NAME"

for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON_SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/$APP_NAME.icns"
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null

echo "$APP_BUNDLE"
