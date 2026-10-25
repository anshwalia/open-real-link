#!/usr/bin/env python3
"""Hardware-free tests for the Windows transport.

The parts of :mod:`tltransport.windows` that need a real device (opening a
channel, moving bytes, an SDP lookup) cannot run here; everything else can --
the address and GUID conversions, the Winsock error classification, and the
``SOCKADDR_BTH`` layout the C API depends on. The module imports on any host,
so these run everywhere; the live paths skip cleanly off Windows.

    python3 tests/test_windows_transport.py
"""

from __future__ import annotations

import ctypes
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
    import tltransport.windows as win
    from tlcore.errors import ChannelRefused, NotConnected, PageTimeout, TransportError

    # --- address conversion ------------------------------------------------
    # The first octet is the most significant byte, exactly like BlueZ str2ba.
    check("parse colon form", win.parse_address("AA:BB:CC:DD:EE:FF"), 0xAABBCCDDEEFF)
    check("parse hyphen form", win.parse_address("AA-BB-CC-DD-EE-FF"), 0xAABBCCDDEEFF)
    check("parse bare hex", win.parse_address("aabbccddeeff"), 0xAABBCCDDEEFF)
    check("format round trip", win.format_address(0xAABBCCDDEEFF), "AA:BB:CC:DD:EE:FF")
    check("format pads", win.format_address(0x000000000001), "00:00:00:00:00:01")
    check_raises("short address rejected", ValueError, win.parse_address, "AA:BB")
    check_raises("non-hex rejected", ValueError, win.parse_address, "ZZ:BB:CC:DD:EE:FF")

    # --- service UUID -> GUID ---------------------------------------------
    check("normalize full", win.normalize_uuid("00001101-0000-1000-8000-00805F9B34FB"),
          "00001101-0000-1000-8000-00805F9B34FB")
    check("normalize short 16-bit", win.normalize_uuid("1101"),
          "00001101-0000-1000-8000-00805F9B34FB")
    check("normalize short 32-bit", win.normalize_uuid("0000079A"),
          "0000079A-0000-1000-8000-00805F9B34FB")
    guid = win.guid_from_uuid("00001101-0000-1000-8000-00805F9B34FB")
    check("guid Data1", guid.Data1, 0x00001101)
    check("guid Data2", guid.Data2, 0x0000)
    check("guid Data3", guid.Data3, 0x1000)
    check("guid Data4", bytes(guid.Data4), bytes.fromhex("800000805f9b34fb"))
    check_raises("bad uuid rejected", ValueError, win.guid_from_uuid, "not-a-uuid")

    # --- Winsock error classification (refusal vs page timeout) ------------
    check("refused", win.classify_wsa_error(win.WSAECONNREFUSED), ChannelRefused)
    check("timeout", win.classify_wsa_error(win.WSAETIMEDOUT), PageTimeout)
    check("host down", win.classify_wsa_error(win.WSAEHOSTDOWN), PageTimeout)
    check("host unreachable", win.classify_wsa_error(win.WSAEHOSTUNREACH), PageTimeout)
    check("other", win.classify_wsa_error(12345), TransportError)
    check_raises("refused raises ChannelRefused", ChannelRefused,
                 win.WindowsTransport._raise_for_status,
                 win.WSAECONNREFUSED, "AA-BB-CC", 12)
    check_raises("timeout raises PageTimeout", PageTimeout,
                 win.WindowsTransport._raise_for_status,
                 win.WSAETIMEDOUT, "AA-BB-CC", 12)
    check_raises("other raises TransportError", TransportError,
                 win.WindowsTransport._raise_for_status, 12345, "AA-BB-CC", 12)

    # --- the address struct the C API reads -------------------------------
    # Regression: ws2bth.h wraps SOCKADDR_BTH in #pragma pack(1), so it is 30
    # bytes with btAddr at offset 2 and port at 26. A naturally aligned layout
    # (40 bytes, btAddr at 8) is silently wrong: connect reads a zero address
    # and fails with WSAEADDRNOTAVAIL, and an SDP result parses to channel 0.
    check("SOCKADDR_BTH is packed", getattr(win.SOCKADDR_BTH, "_pack_", 0), 1)
    check("SOCKADDR_BTH size", ctypes.sizeof(win.SOCKADDR_BTH), 30)
    offsets = {name: getattr(win.SOCKADDR_BTH, name).offset
               for name, _ in win.SOCKADDR_BTH._fields_}
    check("SOCKADDR_BTH field offsets", offsets,
          {"addressFamily": 0, "btAddr": 2, "serviceClassId": 10, "port": 26})
    sa = win.sockaddr_bth("AA-BB-CC-DD-EE-FF", 12)
    check("sockaddr family", sa.addressFamily, win.AF_BTH)
    check("sockaddr address", sa.btAddr, 0xAABBCCDDEEFF)
    check("sockaddr channel", sa.port, 12)
    # The exact 30 bytes handed to connect(): family, little-endian address,
    # empty GUID, little-endian channel. Pinned so any layout drift fails here
    # instead of on hardware.
    check("sockaddr wire bytes", bytes(sa).hex(),
          "2000ffeeddccbbaa0000" + "00" * 16 + "0c000000")
    # And the same layout must parse an SDP result back to its channel -- the
    # half of the bug that made discovery read channel 0. The bytes are built by
    # hand in the packed wire form, not with sockaddr_bth(), so a reader that
    # disagrees with the provider fails here.
    packed = bytes.fromhex("2000ffeeddccbbaa0000" + "00" * 16 + "0c000000")
    raw = ctypes.create_string_buffer(packed, len(packed))
    csa = win.CSADDR_INFO()
    csa.RemoteAddr.lpSockaddr = ctypes.cast(raw, ctypes.c_void_p)
    csa.RemoteAddr.iSockaddrLength = len(packed)
    result = win.WSAQUERYSETW()
    result.dwNumberOfCsAddrs = 1
    result.lpcsaBuffer = ctypes.pointer(csa)
    check("SDP result -> channel",
          win.WindowsTransport._channel_from_result(
              result, win.parse_address("AA-BB-CC-DD-EE-FF")), 12)
    check("SDP result ignores another address",
          win.WindowsTransport._channel_from_result(
              result, win.parse_address("11-22-33-44-55-66")), None)

    # --- a fresh transport needs no radio ---------------------------------
    transport = win.WindowsTransport()
    check("fresh transport is disconnected", transport.is_connected(), False)
    transport.close()  # idempotent, must not raise
    check("close is idempotent", transport.is_connected(), False)
    check("repr", "WindowsTransport" in repr(transport), True)
    check_raises("write while closed", NotConnected, transport.write, b"\x00")
    check_raises("read while closed", NotConnected, transport.read, 0.1)

    # --- the Winsock binding is Windows-only ------------------------------
    if sys.platform != "win32":
        check_raises("winsock is Windows-only", TransportError, win._winsock)
        check_raises("open is Windows-only", TransportError, transport.open,
                     "AA-BB-CC-DD-EE-FF", 12)
        check_raises("discover is Windows-only", TransportError,
                     transport.discover_channel, "AA-BB-CC-DD-EE-FF", win.SPP_UUID)

    print(f"checks run: {CHECKS}")
    if FAILURES:
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("WINDOWS TRANSPORT: OK")
    return 0


def test_windows_transport() -> None:
    """pytest entry point."""
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
