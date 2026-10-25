#!/usr/bin/env python3
"""Guards for the macOS and Windows app bundles and their icons.

The bundles themselves are built by PyInstaller from
``release/macos/open-real-link.spec`` and ``release/windows/open-real-link.spec``
and cannot be produced inside a unit test. What *can* be checked is that the
pieces each spec depends on exist, that the macOS pipeline turns an SVG or PNG
into a `.icns` carrying every size macOS expects, and that the Windows pipeline
turns a PNG into a valid `.ico`.

Run directly for the check list, or under pytest (which needs the aggregator at
the bottom, since the per-test functions only *record* failures).

    python3 tests/test_packaging.py
"""
from __future__ import annotations

import binascii
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
CHECKS = 0

AGGREGATOR = "test_packaging"

SPEC = ROOT / "release" / "macos" / "open-real-link.spec"
ENTRY = ROOT / "release" / "macos" / "entry.py"
MAKE_ICON = ROOT / "release" / "macos" / "make-icon.sh"
ICON = ROOT / "release" / "macos" / "icon.icns"

SHARED_ICON = ROOT / "release" / "icon.png"
WIN_SPEC = ROOT / "release" / "windows" / "open-real-link.spec"
WIN_ENTRY = ROOT / "release" / "windows" / "entry.py"
WIN_BUILD = ROOT / "release" / "windows" / "build.ps1"
WIN_MAKE_ICON = ROOT / "release" / "windows" / "make_icon.py"
WIN_ICON = ROOT / "release" / "windows" / "icon.ico"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

#: The ten representations ``iconutil`` writes and macOS reads.
ICON_SIZES = sorted([
    "icon_16x16.png", "icon_16x16@2x.png",
    "icon_32x32.png", "icon_32x32@2x.png",
    "icon_128x128.png", "icon_128x128@2x.png",
    "icon_256x256.png", "icon_256x256@2x.png",
    "icon_512x512.png", "icon_512x512@2x.png",
])


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def _iconset_names(icns: pathlib.Path, work: pathlib.Path) -> list[str]:
    """Unpack ``icns`` back to an iconset and return its member names."""
    out = work / "roundtrip.iconset"
    subprocess.run(["iconutil", "-c", "iconset", str(icns), "-o", str(out)],
                   check=True, capture_output=True)
    return sorted(p.name for p in out.iterdir())


def test_bundle_inputs_exist() -> None:
    """The spec's entry point and data must be on disk."""
    check("spec exists", SPEC.is_file(), True)
    check("entry exists", ENTRY.is_file(), True)
    check("fixtures dir exists", (ROOT / "fixtures").is_dir(), True)
    check("make-icon exists", MAKE_ICON.is_file(), True)
    if os.name != "nt":
        # Windows has no Unix execute bit, and git does not set one on checkout
        check("make-icon is executable",
              bool(MAKE_ICON.stat().st_mode & 0o111) if MAKE_ICON.is_file() else False,
              True)


def test_committed_icon_has_all_sizes() -> None:
    """If an icon is committed, it must carry every macOS representation.

    No icon is committed yet, so this passes vacuously until one is added; from
    then on it is the guard that stops a half-built iconset from shipping.
    """
    if not ICON.is_file() or not shutil.which("iconutil"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        names = _iconset_names(ICON, pathlib.Path(tmp))
    check("committed icon sizes", names, ICON_SIZES)


def test_make_icon_pipeline() -> None:
    """make-icon.sh turns an SVG into a full .icns (macOS only)."""
    if sys.platform != "darwin" or not (shutil.which("sips") and shutil.which("iconutil")):
        return
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        svg = tmp / "icon.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">'
            '<rect width="1024" height="1024" fill="#4c6ef5"/></svg>',
            encoding="utf-8",
        )
        out = tmp / "icon.icns"
        proc = subprocess.run([str(MAKE_ICON), str(svg), str(out)],
                              capture_output=True, text=True)
        check("make-icon exit", proc.returncode, 0)
        check("icns produced", out.is_file(), True)
        if out.is_file():
            check("generated icon sizes", _iconset_names(out, tmp), ICON_SIZES)


def _tiny_png(width: int, height: int) -> bytes:
    """A minimal opaque RGBA PNG, built without an image library."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_windows_bundle_inputs_exist() -> None:
    """The Windows spec's entry point, data and tooling must be on disk."""
    check("windows spec exists", WIN_SPEC.is_file(), True)
    check("windows entry exists", WIN_ENTRY.is_file(), True)
    check("windows build script exists", WIN_BUILD.is_file(), True)
    check("windows make-icon exists", WIN_MAKE_ICON.is_file(), True)
    check("shared icon source exists", SHARED_ICON.is_file(), True)
    check("release workflow exists", RELEASE_WORKFLOW.is_file(), True)


def test_windows_make_icon_pipeline() -> None:
    """make_icon.py packs a PNG into a valid single-entry .ico (any OS)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        png = tmp / "source.png"
        png.write_bytes(_tiny_png(32, 32))
        out = tmp / "icon.ico"
        proc = subprocess.run(
            [sys.executable, str(WIN_MAKE_ICON), str(png), str(out)],
            capture_output=True, text=True)
        check("make-icon exit", proc.returncode, 0)
        check("ico produced", out.is_file(), True)
        if not out.is_file():
            return
        data = out.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        check("ico reserved field", reserved, 0)
        check("ico type field", kind, 1)
        check("ico entry count", count, 1)
        width, height, _colours, _rsv, planes, bpp, size, offset = \
            struct.unpack("<BBBBHHII", data[6:22])
        check("ico entry dimensions", (width, height), (32, 32))
        check("ico entry planes", planes, 1)
        check("ico entry bpp", bpp, 32)
        check("ico entry size", size, png.stat().st_size)
        check("ico image offset", offset, 22)
        check("ico embeds the PNG", data[offset:offset + size], png.read_bytes())
        check("ico has no trailing bytes", data[offset + size:], b"")


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn) and name != AGGREGATOR:
            fn()
    print(f"checks run: {CHECKS}")
    if FAILURES:
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("PACKAGING: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_packaging() -> None:
    """pytest entry point.

    The per-test functions above only *record* failures, so pytest would pass
    this file however many checks failed. This is the one that turns them into a
    result -- the same shape as ``tests/test_app.py``.
    """
    assert main() == 0
