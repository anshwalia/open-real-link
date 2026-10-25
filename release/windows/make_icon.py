#!/usr/bin/env python3
"""Build ``release/windows/icon.ico`` from a PNG source.

    python release/windows/make_icon.py [SOURCE.png] [OUTPUT.ico]

SOURCE defaults to the shared ``release/icon.png`` (falling back to a local
``release/windows/icon.png``); OUTPUT defaults to ``release/windows/icon.ico``,
which the PyInstaller spec picks up automatically.

Windows app icons are ``.ico`` containers. This packs the source PNG into one
ICO entry with no external tools: ICO entries may hold PNG data directly, which
Windows has understood since Vista, so neither a rasteriser nor Pillow is
required. The entry keeps the source's own pixels -- export a large square
(1024x1024) for the crispest taskbar and Explorer rendering.
"""
from __future__ import annotations

import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
SHARED = HERE.parent / "icon.png"


def png_size(data: bytes) -> tuple[int, int]:
    """Return the ``(width, height)`` from a PNG's IHDR chunk."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    if data[12:16] != b"IHDR":
        raise ValueError("PNG is missing its IHDR chunk")
    return struct.unpack(">II", data[16:24])


def build_ico(source: pathlib.Path, output: pathlib.Path) -> tuple[int, int]:
    """Pack ``source`` (a PNG) into a single-entry ICO at ``output``."""
    png = source.read_bytes()
    width, height = png_size(png)

    def dimension(n: int) -> int:
        # The ICO directory stores a single byte; 256 or larger is encoded as 0.
        if n < 1:
            raise ValueError(f"icon dimension {n}px is invalid")
        return 0 if n >= 256 else n

    header = struct.pack("<HHH", 0, 1, 1)  # reserved, type=icon, one entry
    entry = struct.pack(
        "<BBBBHHII",
        dimension(width), dimension(height),
        0,   # colour count (0 = true colour)
        0,   # reserved
        1,   # colour planes
        32,  # bits per pixel
        len(png),
        len(header) + 16,  # offset of the image data
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + entry + png)
    return width, height


def main(argv: list[str]) -> int:
    source = pathlib.Path(argv[1]) if len(argv) > 1 else None
    output = pathlib.Path(argv[2]) if len(argv) > 2 else HERE / "icon.ico"
    if source is None:
        source = SHARED if SHARED.is_file() else HERE / "icon.png"
    if not source.is_file():
        print(f"make-icon: no source PNG at {source}", file=sys.stderr)
        return 1
    try:
        width, height = build_ico(source, output)
    except ValueError as exc:
        print(f"make-icon: {exc}", file=sys.stderr)
        return 1
    print(f"make-icon: wrote {output} ({width}x{height} from {source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
