#!/usr/bin/env bash
#
# Build OpenRealLink.app for macOS.
#
#   release/macos/build.sh            # build the .app
#   MAKE_DMG=1 release/macos/build.sh # build the .app, then a .dmg around it
#
# Uses the repository virtualenv (../.venv) unless PYTHON is set. Output lands in
# dist/macos/. See docs/packaging.md for signing and notarisation.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi
echo "python: $PYTHON"

# If an icon source is present, (re)build icon.icns so the bundle picks it up.
# A missing or broken icon is not fatal: the build continues without one.
if [[ -f "$HERE/icon.svg" || -f "$HERE/icon.png" ]]; then
  "$HERE/make-icon.sh" || echo "warning: icon build failed; continuing without a custom icon" >&2
fi

# PyInstaller, PySide6 and the IOBluetooth transport are all needed to freeze.
"$PYTHON" -m pip install --quiet -e ".[gui,macos,release]"

echo "building the app bundle..."
"$PYTHON" -m PyInstaller \
  --noconfirm --clean \
  --distpath "$ROOT/dist/macos" \
  --workpath "$ROOT/build/macos" \
  "$HERE/open-real-link.spec"

APP="$ROOT/dist/macos/OpenRealLink.app"
echo "built: $APP"

if [[ "${MAKE_DMG:-0}" == "1" ]]; then
  DMG="$ROOT/dist/macos/OpenRealLink.dmg"
  echo "building the disk image..."
  rm -f "$DMG"
  hdiutil create -volname "OpenRealLink" -srcfolder "$APP" \
    -ov -format UDZO "$DMG" >/dev/null
  echo "built: $DMG"
fi
