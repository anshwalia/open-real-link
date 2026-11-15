#!/usr/bin/env python3
"""Give the host its audio back after a live session.

A live run holds and drops the ACL link repeatedly (``--reset`` on every
``0xE00002BC``, plus one connect per subcommand).  macOS mirrors a Bluetooth
headset's AVRCP **absolute volume** onto a per-device control, so every
reconnect re-syncs the Mac's output volume *from the headset*.  If the buds are
reporting 0 -- out of the ears, or simply at zero -- the Mac's volume is pinned
to 0, and from then on music and video are silent while the audio route still
looks perfectly healthy (device connected, A2DP up, ``afplay`` succeeds).

Nothing about the protocol is wrong when that happens; the host just needs its
volume back.  Run this after every live session:

    python3 tools/host_audio_check.py          # report
    python3 tools/host_audio_check.py --fix    # raise a 0/muted output to 50

It reads only; ``--fix`` sets the output volume (and unmutes) and nothing else.
It does not touch the headset, so it is safe to run with the buds in an ear.

Exit codes: 0 healthy (or fixed), 1 problem found and left alone. On a platform
without the macOS volume model it prints a skip and exits 0.
"""

from __future__ import annotations

import re
import subprocess
import sys

#: Volume to restore when the output is silent.  Chosen to be audible without
#: being a surprise on a worn headset.
FLOOR = 50

#: `osascript -e 'get volume settings'` ->
#: "output volume:0, input volume:50, alert volume:100, output muted:false"
_VOLUME_RE = re.compile(r"output volume:(\d+).*?output muted:(true|false)")


def _run(argv: list[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def volume_settings() -> tuple[int, bool] | None:
    """Return ``(output volume, muted)``, or ``None`` when unavailable."""
    m = _VOLUME_RE.search(_run(["osascript", "-e", "get volume settings"]))
    if not m:
        return None
    return int(m.group(1)), m.group(2) == "true"


def default_output_device() -> str | None:
    """Name of the current default output device, or ``None``."""
    lines = _run(["system_profiler", "SPAudioDataType"]).splitlines()
    for i, line in enumerate(lines):
        if "Default Output Device: Yes" not in line:
            continue
        for back in range(i - 1, max(-1, i - 12), -1):
            name = lines[back].strip()
            if name.endswith(":") and not name.startswith("Default"):
                return name[:-1]
    return None


def main(argv: list[str]) -> int:
    fix = "--fix" in argv
    if sys.platform != "darwin":
        print(f"host audio check: skipped -- {sys.platform} has no macOS volume model")
        return 0

    settings = volume_settings()
    if settings is None:
        print("host audio check: could not read the volume settings; check by ear")
        return 1

    volume, muted = settings
    device = default_output_device() or "(unknown device)"
    print(f"default output : {device}")
    print(f"output volume  : {volume}{'  (muted)' if muted else ''}")

    if volume > 0 and not muted:
        print("RESULT: OK -- the host will be audible")
        return 0

    problem = "muted" if muted else "at volume 0"
    print(f"PROBLEM: the output is {problem}. A live session can leave it this way:")
    print("  macOS re-syncs a Bluetooth headset's absolute volume on every")
    print("  reconnect, so a headset reporting 0 pins the Mac to 0.")
    if not fix:
        print(f"fix: python3 {sys.argv[0]} --fix    (or raise the volume yourself)")
        return 1

    _run(["osascript", "-e", f"set volume output volume {max(FLOOR, volume)}"])
    _run(["osascript", "-e", "set volume without output muted"])
    after = volume_settings()
    if after and after[0] > 0 and not after[1]:
        print(f"FIXED: output volume is now {after[0]}")
        return 0
    print("could not raise the volume; do it in System Settings -> Sound")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
