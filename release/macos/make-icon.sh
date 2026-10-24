#!/usr/bin/env bash
#
# Build release/macos/icon.icns from an SVG or PNG source.
#
#   release/macos/make-icon.sh [SOURCE] [OUTPUT]
#
# SOURCE defaults to release/macos/icon.svg, then release/macos/icon.png.
# OUTPUT defaults to release/macos/icon.icns (which the PyInstaller spec picks up
# automatically). Requires macOS: `sips` and `iconutil` are part of the system.
#
# macOS app icons must be .icns; SVG and PNG are not usable as-is. `sips` reads
# both on recent macOS, but if it cannot rasterise the source this falls back to
# rsvg-convert / cairosvg / inkscape, then fails with instructions.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${1:-}"
OUTPUT="${2:-$HERE/icon.icns}"

if [[ -z "$SOURCE" ]]; then
  for candidate in "$HERE/icon.svg" "$HERE/icon.png"; do
    if [[ -f "$candidate" ]]; then SOURCE="$candidate"; break; fi
  done
fi

if [[ -z "$SOURCE" || ! -f "$SOURCE" ]]; then
  echo "make-icon: no source given and neither icon.svg nor icon.png is present." >&2
  echo "  usage: release/macos/make-icon.sh [SOURCE.svg|SOURCE.png] [OUTPUT.icns]" >&2
  exit 1
fi

for tool in sips iconutil; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "make-icon: '$tool' not found -- this script is macOS-only." >&2
    exit 1
  }
done

ext="$(printf '%s' "${SOURCE##*.}" | tr '[:upper:]' '[:lower:]')"
if [[ "$ext" == "icns" ]]; then
  mkdir -p "$(dirname "$OUTPUT")"
  cp "$SOURCE" "$OUTPUT"
  echo "make-icon: $SOURCE is already .icns; copied to $OUTPUT"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PNG="$WORK/icon1024.png"

# Rasterise any source to a 1024x1024 PNG.
rasterize() {
  local src="$1" out="$2"
  if sips -s format png -z 1024 1024 "$src" --out "$out" >/dev/null 2>&1 && [[ -s "$out" ]]; then
    return 0
  fi
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 1024 -h 1024 "$src" -o "$out" && return 0
  fi
  if command -v cairosvg >/dev/null 2>&1; then
    cairosvg "$src" -o "$out" -W 1024 -H 1024 && return 0
  fi
  if command -v inkscape >/dev/null 2>&1; then
    inkscape "$src" -w 1024 -h 1024 -o "$out" && return 0
  fi
  return 1
}

echo "make-icon: rasterising $SOURCE -> 1024x1024"
if ! rasterize "$SOURCE" "$PNG"; then
  echo "make-icon: could not rasterise '$SOURCE'." >&2
  echo "  'sips' could not read it and no fallback is installed." >&2
  echo "  Install rsvg-convert (brew install librsvg) or cairosvg (pip install cairosvg)," >&2
  echo "  or export a 1024x1024 PNG and pass that instead." >&2
  exit 1
fi

ICONSET="$WORK/icon.iconset"
mkdir -p "$ICONSET"
for pair in \
  "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
  "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
  "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
  # shellcheck disable=SC2086
  set -- $pair
  sips -z "$1" "$1" "$PNG" --out "$ICONSET/$2.png" >/dev/null
done

mkdir -p "$(dirname "$OUTPUT")"
iconutil -c icns "$ICONSET" -o "$OUTPUT"
echo "make-icon: wrote $OUTPUT"
