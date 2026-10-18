#!/usr/bin/env python3
"""Hardware-free tests for the macOS transport.

The parts of :mod:`tltransport.macos` that need a real device (opening a
channel, moving bytes) cannot run here; everything else can -- the IOReturn
classification, link-state reporting, and channel discovery against a stubbed
SDP table. Skips cleanly where pyobjc is not installed.

    python3 tests/test_macos_transport.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def check_raises(label: str, exc_type, fn, *args) -> None:
    global CHECKS
    CHECKS += 1
    try:
        fn(*args)
    except exc_type:
        return
    except Exception as exc:
        FAILURES.append(f"{label}: raised {type(exc).__name__}, expected {exc_type.__name__}")
        return
    FAILURES.append(f"{label}: did not raise {exc_type.__name__}")


def run() -> int:
    try:
        import tltransport.macos as macos
    except ImportError as exc:
        print(f"SKIP: {exc}")
        return 0

    from tlcore.errors import ChannelRefused, PageTimeout, TransportError

    # --- IOReturn classification (the refusal-vs-timeout distinction) -----
    # 0xE00002BC as pyobjc delivers it can also be the signed int
    for status in (0xE00002BC, -536870212):
        check_raises(f"refused 0x{status & 0xFFFFFFFF:08X}", ChannelRefused,
                     macos.MacOSTransport._raise_for_status, status, "AA-BB-CC", 12)
    check_raises("page timeout", PageTimeout,
                 macos.MacOSTransport._raise_for_status, 0xE00002D6, "AA-BB-CC", 12)
    check_raises("other IOReturn", TransportError,
                 macos.MacOSTransport._raise_for_status, 0x12345678, "AA-BB-CC", 12)
    check_raises("no channel, no status", TransportError,
                 macos.MacOSTransport._raise_for_status, None, "AA-BB-CC", 12)

    # --- fresh transport, no hardware -------------------------------------
    transport = macos.MacOSTransport()
    check("fresh transport is disconnected", transport.is_connected(), False)
    transport.close()  # idempotent, must not raise
    check("close is idempotent", transport.is_connected(), False)
    check("repr", "MacOSTransport" in repr(transport), True)

    # --- UUID normalisation (regression: a friendly label must never be
    #     returned here, or channel discovery silently fails) -------------
    class _Element:
        def __init__(self, text):
            self.text = text

        def __str__(self):
            return self.text

    check("SPP uuid normalises",
          macos._uuid_of(_Element("uuid128(00 00 11 01 00 00 10 00 80 00 00 80 5f 9b 34 fb)")),
          "00001101-0000-1000-8000-00805F9B34FB")
    check("an unrecognised element is passed through",
          macos._uuid_of(_Element("some unknown service")), "some unknown service")

    # --- channel discovery against a stubbed SDP table --------------------
    original = macos.sdp_services
    macos.sdp_services = lambda address: [
        {"index": 0, "channel": 15, "uuid": "0000079A-D102-11E1-9B23-00025B00A5A5"},
        {"index": 1, "channel": 12, "uuid": "00001101-0000-1000-8000-00805F9B34FB"},
        {"index": 2, "channel": None, "uuid": "00001101-0000-1000-8000-00805F9B34FB"},
    ]
    try:
        check("discover SPP channel",
              transport.discover_channel("x", "00001101-0000-1000-8000-00805F9B34FB"), 12)
        check("discover OPPO channel",
              transport.discover_channel("x", "0000079A-D102-11E1-9B23-00025B00A5A5"), 15)
        check("discover honours a short prefix",
              transport.discover_channel("x", "0000079A"), 15)
        check("discover missing uuid",
              transport.discover_channel("x", "DEADBEEF-0000-0000-0000-000000000000"), None)
    finally:
        macos.sdp_services = original

    print(f"checks run: {CHECKS}")
    if FAILURES:
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("MACOS TRANSPORT: OK")
    return 0


def test_macos_transport() -> None:
    """pytest entry point."""
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
