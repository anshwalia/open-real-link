#!/usr/bin/env python3
"""Offline tests for :mod:`tlcore.backup` -- capture and restore round-trips.

Self-contained: builds synthetic recordings on the fly, so it needs no hardware
and no fixtures.  Run directly, or under pytest (the entry point asserts).

    python3 tests/test_backup.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore as t  # noqa: E402
from tltransport.replay import ReplayTransport  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def wire(cmd: int, payload: bytes = b"", mtu: int = 512) -> bytes:
    return b"".join(t.build_frames(t.build_packet(cmd, payload), mtu))


def replay(records) -> tuple[ReplayTransport, t.Session]:
    transport = ReplayTransport(records)
    transport.open("replay", 12)
    return transport, t.Session(transport, cmd_interval=0.0, read_timeout=0.5)


# The Air7 Pro's capability bitmap, plus a non-empty bass-engine reading (the
# live unit advertises bit 38 but answers ``00 00``).
BITMAP = "00bf4f5ae665dc4004"

CANNED = {
    (0x0101, b""): "00c008",
    (0x0103, b""): "00124c06",
    (0x0104, b""): "00000001",
    (0x0105, b""): "00" + b"1,1,111,1,2,1.1.0.85".hex(),
    (0x0107, b""): "00010103",
    (0x0108, b""): "0002" + "02010100" + "02010201",
    (0x010B, b""): "0006",
    (0x010C, b"\x01"): "00010101",
    (0x010C, b"\x02"): "00020110",
    (0x010D, b""): "0000",
    (0x010F, b""): "0004",
    (0x0110, b""): "0001",
    (0x0112, b""): "0001ff",
    (0x0118, b""): "0001012c00",
    (0x011A, b""): "0000",
    (0x0122, b""): "0001010102040000",
    (0x0124, b""): "00010503",   # status 0, min 1, max 5, current 3
    (0x0131, b""): "000000",
}


def backup_records(canned=None, bitmap: str = BITMAP) -> list:
    canned = CANNED if canned is None else canned
    supported = set(t.parse_capabilities(bytes.fromhex(bitmap))["commands"]) | set(t.ALWAYS_ON)
    records = [("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(bitmap)))]
    for cmd, payload, _key in t.READ_PLAN:
        if cmd not in supported:
            continue
        records.append(("tx", wire(cmd, payload)))
        records.append(("rx", wire(cmd | 0x8000, bytes.fromhex(canned[(cmd, payload)]))))
    return records


def check_payload_builder() -> None:
    check("3-byte form", t.payload_set_bass_engine(1, 5, 3), b"\x01\x05\x03")
    check("4-byte form (type>0)", t.payload_set_bass_engine(1, 5, 3, 2), b"\x01\x05\x03\x02")
    check("type -1 is the 3-byte form", t.payload_set_bass_engine(1, 5, 3, -1), b"\x01\x05\x03")
    check("type 0 is the 3-byte form", t.payload_set_bass_engine(1, 5, 3, 0), b"\x01\x05\x03")
    check("bytes are masked", t.payload_set_bass_engine(0x101, 0x05, 0x03), b"\x01\x05\x03")


def check_decoder() -> None:
    check("single", t.parse_bass_engine(bytes.fromhex("00010503")),
          {"status": 0, "value": {"min": 1, "max": 5, "current": 3, "type": None}})
    check("with type", t.parse_bass_engine(bytes.fromhex("0001050302")),
          {"status": 0, "value": {"min": 1, "max": 5, "current": 3, "type": 2}})
    check("empty reply", t.parse_bass_engine(bytes.fromhex("0000")),
          {"status": 0, "value": None})
    check("failure status", t.parse_bass_engine(bytes.fromhex("0e010503")),
          {"status": 14, "value": None})
    check("no bytes", t.parse_bass_engine(b""), {"status": 0, "value": None})
    check("wired into the state table",
          t.decode_state(0x8124, bytes.fromhex("00010503"))["value"]["current"], 3)


def check_capture() -> None:
    transport, session = replay(backup_records())
    snap = t.capture(session)
    check("format", snap["format"], t.BACKUP_FORMAT)
    check("bass captured raw", snap["raw"]["bass_engine"]["reply"], "00010503")
    check("bass derived", snap["settings"]["bass_engine"],
          {"min": 1, "max": 5, "current": 3, "type": None})
    check("bass is restorable", "bass_engine" in t.backup.RESTORABLE, True)
    check("key function still derived",
          snap["settings"]["key_function"], "02" + "02010100" + "02010201")
    check("eq mode still derived", snap["settings"]["eq_mode"], 4)
    check("high volume derived", snap["settings"]["high_volume"], True)
    check("high volume is restorable", "high_volume" in t.backup.RESTORABLE, True)
    # the live unit's empty reply derives nothing
    empty = {**CANNED, (0x0124, b""): "0000"}
    _, session = replay(backup_records(empty))
    check("empty bass reply derives nothing",
          "bass_engine" in t.capture(session)["settings"], False)


def check_restore_bass() -> None:
    _, session = replay(backup_records())
    snap = t.capture(session)

    records = [("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(BITMAP))),
               ("tx", wire(0x0404, bytes([1, 1, 1]))), ("rx", wire(0x8404, bytes([0]))),
               ("tx", wire(0x0401, bytes.fromhex(snap["settings"]["key_function"]))),
               ("rx", wire(0x8401, bytes([0]))),
               ("tx", wire(0x0406, bytes([4]))), ("rx", wire(0x8406, bytes([0]))),
               ("tx", wire(0x0407, bytes([1]))), ("rx", wire(0x8407, bytes([0]))),
               ("tx", wire(0x041B, bytes([1, 5, 3]))), ("rx", wire(0x841B, bytes([0])))]
    transport, session = replay(records)
    report = t.restore(session, snap)
    check("bass reported applied",
          any(a["setting"] == "bass_engine" for a in report["applied"]), True)
    check("high volume reported applied",
          any(a["setting"] == "high_volume" for a in report["applied"]), True)
    check("bass frame written", transport.writes[-1], wire(0x041B, bytes([1, 5, 3])))
    check("bass not listed as not_restored",
          any(s["setting"] == "bass_engine" for s in report["not_restored"]), False)
    check("eq_info still captured-but-not-restored",
          any(s["setting"] == "eq_info" for s in report["not_restored"]), True)

    # dry-run must not write the bass frame
    transport, session = replay(records)
    dry = t.restore(session, snap, dry_run=True)
    check("dry-run reports bass", any(a["setting"] == "bass_engine" for a in dry["applied"]), True)
    check("dry-run wrote nothing past negotiate", len(transport.writes), 1)


def check_restore_unsupported() -> None:
    """A snapshot with a bass value but no bit 38 must not write 0x041B."""
    # bitmap without bit 38: clear it (bitmap byte 4, 0x65 -> 0x25)
    bitmap = "00bf4f5ae625dc4004"
    _, session = replay([("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(bitmap)))])
    snap = {"format": t.BACKUP_FORMAT, "settings": {"bass_engine":
            {"min": 1, "max": 5, "current": 3, "type": None}}}
    report = t.restore(session, snap)
    check("unsupported bass not applied",
          any(a["setting"] == "bass_engine" for a in report["applied"]), False)
    check("unsupported bass reported",
          any(s["setting"] == "bass_engine" for s in report["not_restored"]), True)


def main() -> int:
    for fn in (check_payload_builder, check_decoder, check_capture,
               check_restore_bass, check_restore_unsupported):
        fn()
    if FAILURES:
        print(f"checks run: {CHECKS}")
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"checks run: {CHECKS}")
    print("BACKUP: OK")
    return 0


def test_backup() -> None:
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
