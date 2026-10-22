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


# The Air7 Pro's capability bitmap.
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


def check_removed_symbols() -> None:
    """The three ids that could not be live-verified must stay gone.

    ``0x0124`` is never answered by the Air7 Pro; ``0x0418`` needs a band table
    that only the vendor EQ editor can supply; ``0x0403`` is accepted but a
    provable no-op.  See ``docs/command-coverage.md`` and the live run.
    """
    for name in ("parse_bass_engine", "payload_set_eq",
                 "payload_set_switch_feature"):
        check(f"{name} is not exported", hasattr(t, name), False)
        check(f"{name} is not in __all__", name in t.__all__, False)
    check("0x0124 is not polled", 0x0124 in [c for c, _, _ in t.READ_PLAN], False)
    check("0x041B builder survives (it is live-verified)",
          hasattr(t, "payload_set_bass_engine"), True)
    check("0x0508 decoder survives (it is live-verified)",
          hasattr(t, "parse_bass_engine_status"), True)


def check_payload_builder() -> None:
    check("3-byte form", t.payload_set_bass_engine(1, 5, 3), b"\x01\x05\x03")
    check("4-byte form (type>0)", t.payload_set_bass_engine(1, 5, 3, 2), b"\x01\x05\x03\x02")
    check("type -1 is the 3-byte form", t.payload_set_bass_engine(1, 5, 3, -1), b"\x01\x05\x03")
    check("type 0 is the 3-byte form", t.payload_set_bass_engine(1, 5, 3, 0), b"\x01\x05\x03")
    check("bytes are masked", t.payload_set_bass_engine(0x101, 0x05, 0x03), b"\x01\x05\x03")


def check_capture() -> None:
    transport, session = replay(backup_records())
    snap = t.capture(session)
    check("format", snap["format"], t.BACKUP_FORMAT)
    check("key function still derived",
          snap["settings"]["key_function"], "02" + "02010100" + "02010201")
    check("eq mode still derived", snap["settings"]["eq_mode"], 4)
    check("high volume derived", snap["settings"]["high_volume"], True)
    check("noise mode derived", snap["settings"]["noise_mode"], 1)
    # the two settings that lost their restorer are captured but not derived
    check("switch features captured", "switch_features" in snap["raw"], True)
    check("switch features not derived",
          "switch_features" in snap["settings"], False)
    check("bass engine not captured at all", "bass_engine" in snap["raw"], False)
    check("restorable is the four symmetric settings",
          sorted(t.backup.RESTORABLE),
          ["eq_mode", "high_volume", "key_function", "noise_mode"])
    check("switch_features is in SETTING_KEYS, so it is still reported",
          "switch_features" in t.backup.SETTING_KEYS, True)


def check_restore() -> None:
    _, session = replay(backup_records())
    snap = t.capture(session)

    records = [("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(BITMAP))),
               ("tx", wire(0x0404, bytes([1, 1, 1]))), ("rx", wire(0x8404, bytes([0]))),
               ("tx", wire(0x0401, bytes.fromhex(snap["settings"]["key_function"]))),
               ("rx", wire(0x8401, bytes([0]))),
               ("tx", wire(0x0406, bytes([4]))), ("rx", wire(0x8406, bytes([0]))),
               ("tx", wire(0x0407, bytes([1]))), ("rx", wire(0x8407, bytes([0])))]
    transport, session = replay(records)
    report = t.restore(session, snap)
    check("all four symmetric settings applied",
          sorted(a["setting"] for a in report["applied"]),
          ["eq_mode", "high_volume", "key_function", "noise_mode"])
    check("no 0x041B frame was written",
          any(w[5:7] == b"\x1b\x04" for w in transport.writes), False)
    check("no 0x0403 frame was written",
          any(w[5:7] == b"\x03\x04" for w in transport.writes), False)
    check("eq_info is captured-but-not-restored",
          any(s["setting"] == "eq_info" for s in report["not_restored"]), True)
    check("switch_features is captured-but-not-restored",
          any(s["setting"] == "switch_features" for s in report["not_restored"]), True)
    check("bass_engine is not mentioned at all",
          any("bass" in s["setting"] for s in report["not_restored"]), False)
    check("four writes went out (0x0404/0x0401/0x0406/0x0407)",
          len(transport.writes), 5)

    # dry-run must not write anything past the capability negotiate
    transport, session = replay(records)
    dry = t.restore(session, snap, dry_run=True)
    check("dry-run reports every restorable setting",
          sorted(a["setting"] for a in dry["applied"]),
          ["eq_mode", "high_volume", "key_function", "noise_mode"])
    check("dry-run wrote nothing past negotiate", len(transport.writes), 1)


def check_restore_unsupported() -> None:
    """A setting whose write the device does not advertise must not be written."""
    # the Air7 Pro bitmap with bit 8 ([0x010C, 0x0404]) cleared
    bitmap = "00bf4e5ae665dc4004"
    _, session = replay([("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(bitmap)))])
    snap = {"format": t.BACKUP_FORMAT, "settings": {"noise_mode": 1}}
    report = t.restore(session, snap)
    check("unsupported noise mode not applied", report["applied"], [])
    check("unsupported noise mode reported",
          report["not_restored"],
          [{"setting": "noise_mode", "reason": "unsupported by device"}])


def main() -> int:
    for fn in (check_removed_symbols, check_payload_builder, check_capture,
               check_restore, check_restore_unsupported):
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
