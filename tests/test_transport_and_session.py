#!/usr/bin/env python3
"""Offline tests for ``tltransport`` + ``tlcore.Session`` + ``tlcli``.

Everything here runs against the recorded fixtures under ``fixtures/`` or against
synthetic recordings built on the fly, so it needs no Bluetooth hardware.

    python3 tests/test_transport_and_session.py
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore as t  # noqa: E402 - after the sys.path insert above
from tlcore.errors import DeviceRejected, NotWorn, Timeout  # noqa: E402
from tltransport.replay import ReplayMismatch, ReplayTransport  # noqa: E402

#: name of the pytest aggregator -- main() must skip it or it recurses
AGGREGATOR = "test_transport_and_session"

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def check_raises(label: str, exc_type, fn, *args, **kwargs) -> None:
    global CHECKS
    CHECKS += 1
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as exc:
        FAILURES.append(f"{label}: raised {type(exc).__name__}, expected {exc_type.__name__}")
        return
    FAILURES.append(f"{label}: did not raise {exc_type.__name__}")


def wire(cmd: int, payload: bytes = b"", mtu: int = 512) -> bytes:
    return b"".join(t.build_frames(t.build_packet(cmd, payload), mtu))


def replay(records, **kwargs) -> tuple[ReplayTransport, t.Session]:
    transport = ReplayTransport(records, **kwargs)
    transport.open("replay", 12)
    return transport, t.Session(transport, cmd_interval=0.0, read_timeout=0.5)


# --------------------------------------------------------------------------
def test_protocol_conformance() -> None:
    check("ReplayTransport implements Transport",
          isinstance(ReplayTransport([]), t.Transport), True)
    check("for_platform returns a Transport subclass",
          issubclass(__import__("tltransport").for_platform("replay"), object), True)


def test_crc16_ccitt() -> None:
    # CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection) check values,
    # so the ported helper is honest even without TL_REFERENCE.
    check("crc16(empty)", t.crc16_ccitt(b""), 0xFFFF)
    check("crc16('123456789')", t.crc16_ccitt(b"123456789"), 0x29B1)
    check("crc16(init=0)", t.crc16_ccitt(b"123456789", init=0), 0x31C3)


def test_wire_edges() -> None:
    # varint: zero is a single 0x00, and multi-byte values round-trip
    check("varint_encode(0)", t.varint_encode(0), b"\x00")
    check("varint_decode(0x00)", t.varint_decode(b"\x00"), (0, 1))
    for value in (1, 127, 128, 300, 0x3FFF):
        encoded = t.varint_encode(value)
        check(f"varint round-trip {value}", t.varint_decode(encoded), (value, len(encoded)))
    # nothing in, nothing out
    check("build_frames(empty)", t.build_frames(b""), [])
    # a frame under its 4-byte minimum, and one with a bad SOF
    check_raises("parse_frame too short", t.FrameError, t.parse_frame, b"\xaa\x01")
    check_raises("parse_frame bad SOF", t.FrameError, t.parse_frame, b"\x00\x01\x00\x00")
    # a declared length under the minimum is dropped as junk, not misparsed
    framed = t.StreamDecoder()
    events = framed.feed(b"\xaa\x01\x00")
    check("short frame is junk", events[0]["kind"] if events else None, "junk")
    # the raw decoder drops a non-packet run as junk
    raw = t.StreamDecoder(raw=True)
    events = raw.feed(b"\xff" * 6)
    check("raw junk", events[0]["kind"] if events else None, "junk")
    # an unterminated varint is held back, never misparsed
    held = t.StreamDecoder()
    check("unterminated varint yields nothing", held.feed(b"\xaa\x80\x80"), [])
    check("unterminated varint is buffered", held.pending(), 3)


def test_decoder_short_bodies() -> None:
    # every decoder survives an empty body instead of raising IndexError
    check("public event (empty)", t.parse_public_event(b""),
          {"flags": None, "volume": None, "ai": None})
    check("remote vendor id (empty)", t.parse_remote_vendor_id(b""),
          {"status": 0, "vendor_id": None})
    check("color id (empty)", t.parse_color_id(b""),
          {"status": 0, "color_id": None})
    check("high volume (empty)", t.parse_high_volume(b""),
          {"status": 0, "enabled": None})
    check("ai status (empty)", t.parse_ai_status(b""),
          {"status": 0, "state": None, "type": None})
    check("key function (empty)", t.parse_key_function(b""),
          {"status": 0, "count": 0, "records": []})


def test_strict_matching() -> None:
    transport, session = replay([("tx", wire(0x010C, bytes([1]))),
                                 ("rx", wire(0x810C, bytes([0, 1, 1, 1])))])
    check("read_noise_mode decodes", session.read_noise_mode(), 1)

    transport, session = replay([("tx", wire(0x010C, bytes([1])))])
    check_raises("wrong write is a mismatch", ReplayMismatch,
                 session.send, 0x010C, bytes([2]))

    transport, session = replay([("tx", wire(0x010C, bytes([1])))], strict=False)
    session.send(0x010C, bytes([2]))  # loose mode accepts it
    check("loose mode records the write", transport.writes[-1],
          wire(0x010C, bytes([2])))


def test_capability_gating() -> None:
    transport, session = replay([("tx", wire(0x0100)),
                                 ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004")))])
    caps = session.negotiate_capabilities()
    check("capability ok", caps["ok"], True)
    check("Air7 Pro command count", len(caps["commands"]), 49)
    check("supports 0x0404", session.supports(0x0404), True)
    check("supports 0x0418", session.supports(0x0418), True)
    check("always-on 0x0100", session.supports(0x0100), True)
    check("unsupported id", session.supports(0x0F01), False)


def test_read_status_fixture() -> None:
    transport = ReplayTransport.from_fixture("spp-smoketest.jsonl")
    transport.open("replay", 12)
    session = t.Session(transport, cmd_interval=0.0, read_timeout=0.5)
    session.negotiate_capabilities()
    status = session.read_status()

    check("model", status["model"]["model_id"], 0x064C12)
    check("firmware", status["firmware"]["versions"], ["1.1.0.85"])
    check("version value", status["version"]["value"], 0x08C0)
    check("battery L", status["battery"]["devices"]["L"], {"level": 100, "charging": False})
    check("battery R", status["battery"]["devices"]["R"], {"level": 100, "charging": False})
    check("battery case", status["battery"]["devices"]["CASE"], {"level": 0, "charging": False})
    check("wear in_ear", status["wear"]["in_ear"], True)
    check("noise mode", status["noise_mode"], t.NOISE_MODE_NORMAL)
    check("use time L", status["use_time"]["devices"]["L"], 57551)


def test_multi_bass_engine() -> None:
    """``0x012C`` -> ``0x812C``: the multi-band bass read, on the live reply.

    The body is the one a real Buds Air7 Pro answered on 2026-10-22.  Note the
    values are **signed**: ``fb`` is ``-5``, not ``251``.
    """
    live = bytes.fromhex("0003fb050001fb050002fb050003")
    transport, session = replay([
        ("tx", wire(0x0100)),
        ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
        ("tx", wire(0x012C)),
        ("rx", wire(0x812C, live)),
    ])
    session.negotiate_capabilities()
    result = session.read_multi_bass_engine()
    check("status", result["status"], 0)
    check("count", result["count"], 3)
    check("entries", result["entries"], [
        {"min": -5, "max": 5, "current": 0, "type": 1},
        {"min": -5, "max": 5, "current": 0, "type": 2},
        {"min": -5, "max": 5, "current": 0, "type": 3},
    ])
    check("the request carries no payload", transport.writes[-1], wire(0x012C))
    check("the decoder is wired into the state table",
          t.decode_state(0x812C, live)["entries"][0]["min"], -5)


def test_multi_bass_engine_edges() -> None:
    """Short and truncated bodies must not raise -- ``CommandUtil.l`` returns
    null on a bad length, so the decoder degrades instead."""
    check("empty", t.parse_multi_bass_engine(b""),
          {"status": 0, "count": 0, "entries": []})
    check("status only", t.parse_multi_bass_engine(b"\x00"),
          {"status": 0, "count": 0, "entries": []})
    check("count with no records", t.parse_multi_bass_engine(b"\x00\x03"),
          {"status": 0, "count": 3, "entries": []})
    check("a failure status is reported, not hidden",
          t.parse_multi_bass_engine(bytes.fromhex("0e03"))["status"], 14)
    # a record with only three bytes leaves `type` unknown rather than inventing one
    check("truncated type", t.parse_multi_bass_engine(bytes.fromhex("0001fb0500")),
          {"status": 0, "count": 1,
           "entries": [{"min": -5, "max": 5, "current": 0, "type": None}]})
    # count says 3 but only two records are present: stop, do not pad
    check("count overruns the body",
          len(t.parse_multi_bass_engine(
              bytes.fromhex("0003fb050001fb050002"))["entries"]), 2)


def test_mode_cycle_fixture() -> None:
    transport = ReplayTransport.from_fixture("mode-cycle.jsonl")
    transport.open("replay", 12)
    session = t.Session(transport, cmd_interval=0.0, read_timeout=0.5)

    check("initial mode", session.read_noise_mode(), t.NOISE_MODE_NORMAL)
    # the recording then applies a sequence of mode writes
    for value in (t.NOISE_MODE_NORMAL, t.NOISE_MODE_TRANSPARENCY,
                  t.NOISE_MODE_ANC_LIGHT, t.NOISE_MODE_ANC_MEDIUM):
        check(f"set mode 0x{value:02X}", session.set_noise_mode(value), 0)
    check("push events were collected", any(p["cmd"] == 0x0204 for p in session.events), True)


# -- backup / restore ------------------------------------------------------
def _canned_replies() -> dict:
    return {
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
        (0x0124, b""): "0000",
        (0x0131, b""): "000000",
    }


def _backup_records(canned, bitmap="00bf4f5ae665dc4004") -> list:
    supported = set(t.parse_capabilities(bytes.fromhex(bitmap))["commands"]) | set(t.ALWAYS_ON)
    records = [("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(bitmap)))]
    for cmd, payload, _key in t.READ_PLAN:
        if cmd not in supported:
            continue
        records.append(("tx", wire(cmd, payload)))
        records.append(("rx", wire(cmd | 0x8000, bytes.fromhex(canned[(cmd, payload)]))))
    return records


def test_backup_restore() -> None:
    canned = _canned_replies()
    transport, session = replay(_backup_records(canned))
    snapshot = t.capture(session)

    check("backup format", snapshot["format"], t.BACKUP_FORMAT)
    check("backup model", snapshot["device"]["model"], "0x064C12")
    check("backup firmware", snapshot["device"]["firmware"], ["1.1.0.85"])
    check("backup noise mode", snapshot["settings"]["noise_mode"], 1)
    check("backup key-function payload", snapshot["settings"]["key_function"],
          "02" + "02010100" + "02010201")
    # switch_features is captured raw but no longer derived: its write (0x0403)
    # is accepted yet a provable no-op, so it has no restorer
    check("backup switch features not derived",
          "switch_features" in snapshot["settings"], False)
    check("backup eq mode", snapshot["settings"]["eq_mode"], 4)
    check("backup raw captured 0x0108", snapshot["raw"]["key_function"]["reply"],
          "0002" + "02010100" + "02010201")
    check("decode 0x8108", t.parse_key_function(
        bytes.fromhex(snapshot["raw"]["key_function"]["reply"]))["count"], 2)

    kf_payload = bytes.fromhex(snapshot["settings"]["key_function"])
    records = [("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
               ("tx", wire(0x0404, bytes([1, 1, 1]))), ("rx", wire(0x8404, bytes([0]))),
               ("tx", wire(0x0401, kf_payload)), ("rx", wire(0x8401, bytes([0]))),
               ("tx", wire(0x0406, bytes([4]))), ("rx", wire(0x8406, bytes([0]))),
               ("tx", wire(0x0407, bytes([1]))), ("rx", wire(0x8407, bytes([0])))]
    transport, session = replay(records)
    report = t.restore(session, snapshot)
    check("restore noise first", report["applied"][0]["setting"], "noise_mode")
    check("restore applied key_function",
          any(a["setting"] == "key_function" for a in report["applied"]), True)
    check("restore applied eq_mode",
          any(a["setting"] == "eq_mode" for a in report["applied"]), True)
    check("restore applied high_volume",
          any(a["setting"] == "high_volume" for a in report["applied"]), True)
    check("restore wrote the noise frame", transport.writes[1], wire(0x0404, bytes([1, 1, 1])))
    check("restore reports eq as unrestorable",
          any(s["setting"] == "eq_info" for s in report["not_restored"]), True)

    # dry-run must not consume a write
    transport, session = replay(records)
    dry = t.restore(session, snapshot, dry_run=True)
    check("dry-run reports noise", dry["applied"][0]["setting"], "noise_mode")
    check("dry-run wrote nothing past negotiate", len(transport.writes), 1)

    check_raises("restore rejects a foreign file", ValueError,
                 t.restore, session, {"format": "nope"})


def test_cli_backup_restore() -> None:
    from tlcli.cli import main

    canned = _canned_replies()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        cap_path = tmp / "backup.jsonl"
        cap_path.write_text("\n".join(
            json.dumps({"dir": d, "hex": b.hex()}) for d, b in _backup_records(canned)), encoding="utf-8")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(cap_path), "backup", "-"])
        check("cli backup rc", rc, 0)
        snapshot = json.loads(buf.getvalue())
        check("cli backup noise mode", snapshot["settings"]["noise_mode"], 1)

        snap_path = tmp / "snapshot.json"
        snap_path.write_text(json.dumps(snapshot), encoding="utf-8")
        rec_path = tmp / "restore.jsonl"
        rec_path.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0404, bytes([1, 1, 1]))), ("rx", wire(0x8404, bytes([0]))),
            ("tx", wire(0x0401, bytes.fromhex(snapshot["settings"]["key_function"]))),
            ("rx", wire(0x8401, bytes([0]))),
            ("tx", wire(0x0406, bytes([snapshot["settings"]["eq_mode"]]))),
            ("rx", wire(0x8406, bytes([0]))),
            # restore also writes the loudness boost; this entry was missing while
            # the per-test checks were unenforced under pytest (see
            # test_transport_and_session at the end of this file)
            ("tx", wire(0x0407, bytes([snapshot["settings"]["high_volume"]]))),
            ("rx", wire(0x8407, bytes([0])))]), encoding="utf-8")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(rec_path), "restore", "--dry-run", str(snap_path)])
        check("cli restore dry-run rc", rc, 0)
        check("cli restore dry-run text", "[DRY-RUN] noise_mode" in buf.getvalue(), True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(rec_path), "restore", str(snap_path)])
        check("cli restore rc", rc, 0)
        check("cli restore applied", "[APPLIED] key_function" in buf.getvalue(), True)


def test_eq_session() -> None:
    transport, session = replay([
        ("tx", wire(0x010F)), ("rx", wire(0x810F, bytes.fromhex("0004"))),
        ("tx", wire(0x0122)), ("rx", wire(0x8122, bytes.fromhex("0001010102040000"))),
        ("tx", wire(0x0406, bytes([2]))), ("rx", wire(0x8406, bytes([0]))),
        ("tx", wire(0x010F)), ("rx", wire(0x810F, bytes.fromhex("0002"))),
    ])
    check("read_eq_mode", session.read_eq_mode(), 4)
    check("read_eq_info eqId", session.read_eq_info()["entries"][0]["eq_id"], 4)
    session.set_eq_mode(2)
    check("set_eq_mode then read", session.read_eq_mode(), 2)


def test_high_volume_session() -> None:
    # the live-verified round trip (Air7 Pro, 2026-10-20):
    #   0x0110 -> 00 01, 0x0407 00 -> 00 00, 0x0407 01 -> 00 01
    transport, session = replay([
        ("tx", wire(0x0110)), ("rx", wire(0x8110, bytes.fromhex("0001"))),
        ("tx", wire(0x0407, bytes([0]))), ("rx", wire(0x8407, bytes([0]))),
        ("tx", wire(0x0110)), ("rx", wire(0x8110, bytes.fromhex("0000"))),
        ("tx", wire(0x0407, bytes([1]))), ("rx", wire(0x8407, bytes([0]))),
        ("tx", wire(0x0110)), ("rx", wire(0x8110, bytes.fromhex("0001"))),
    ])
    check("read_high_volume initial", session.read_high_volume(), True)
    session.set_high_volume(False)
    check("high volume off after 0x0407 00", session.read_high_volume(), False)
    session.set_high_volume(True)
    check("high volume restored by 0x0407 01", session.read_high_volume(), True)
    check("payload_set_high_volume(True)", t.payload_set_high_volume(True), b"\x01")
    check("payload_set_high_volume(False)", t.payload_set_high_volume(False), b"\x00")
    check("parse_high_volume on", t.parse_high_volume(bytes.fromhex("0001")),
          {"status": 0, "enabled": True})


def test_find_mode_session() -> None:
    # the live-verified round trip (Air7 Pro, 2026-10-20):
    #   0x0400 01 -> 0x8400 00, then 0x0400 00 -> 0x8400 00.  No read pair.
    transport, session = replay([
        ("tx", wire(0x0400, bytes([1]))), ("rx", wire(0x8400, bytes([0]))),
        ("tx", wire(0x0400, bytes([0]))), ("rx", wire(0x8400, bytes([0]))),
    ])
    check("set_find_mode(True) status", session.set_find_mode(True), 0)
    check("set_find_mode(False) status", session.set_find_mode(False), 0)
    check("payload_set_find_mode(True)", t.payload_set_find_mode(True), b"\x01")
    check("payload_set_find_mode(False)", t.payload_set_find_mode(False), b"\x00")
    # find mode has no read half: the ack is the only confirmation
    check("no read_find_mode", hasattr(t.Session, "read_find_mode"), False)


def test_ai_status_session() -> None:
    # the live-verified round trip (Air7 Pro, 2026-10-20):
    #   0x0131 -> 00 00 00, 0x0428 01 01 -> 00 01 01, 0x0428 00 01 -> 00 00 00
    transport, session = replay([
        ("tx", wire(0x0131)), ("rx", wire(0x8131, bytes.fromhex("000000"))),
        ("tx", wire(0x0428, bytes([1, 1]))), ("rx", wire(0x8428, bytes([0]))),
        ("tx", wire(0x0131)), ("rx", wire(0x8131, bytes.fromhex("000101"))),
        ("tx", wire(0x0428, bytes([0, 1]))), ("rx", wire(0x8428, bytes([0]))),
        ("tx", wire(0x0131)), ("rx", wire(0x8131, bytes.fromhex("000000"))),
    ])
    check("read_ai_status idle", session.read_ai_status(), {"state": 0, "type": 0})
    session.set_ai_status(1, 1)
    check("read_ai_status active", session.read_ai_status(), {"state": 1, "type": 1})
    session.set_ai_status(0, 1)
    check("read_ai_status cleared", session.read_ai_status(), {"state": 0, "type": 0})
    check("payload_set_ai_status(1,1)", t.payload_set_ai_status(1, 1), b"\x01\x01")


def test_switch_feature_session() -> None:
    # the live-verified read (Air7 Pro, 2026-10-20): 0x010D -> 00 00, i.e. the
    # unit advertises the capability and exposes no switch features at all.
    transport, session = replay([
        ("tx", wire(0x010D)), ("rx", wire(0x810D, bytes.fromhex("0000"))),
    ])
    check("read_switch_features empty", session.read_switch_features(),
          {"status": 0, "entries": []})
    check("the request carried no payload", transport.writes[0], wire(0x010D))

    # and with records, so the pair walk is exercised too
    _, session2 = replay([
        ("tx", wire(0x010D)),
        ("rx", wire(0x810D, bytes.fromhex("000211011200"))),
    ])
    check("read_switch_features pairs", session2.read_switch_features(),
          {"status": 0, "entries": [{"feature": 0x11, "enabled": 1},
                                    {"feature": 0x12, "enabled": 0}]})
    # no write half ships: 0x0403 is accepted by the device but changes nothing
    check("no set_switch_feature", hasattr(t.Session, "set_switch_feature"), False)
    check("payload_set_ai_status(0,4)", t.payload_set_ai_status(0, 4), b"\x00\x04")
    check("parse_ai_status", t.parse_ai_status(bytes.fromhex("000101")),
          {"status": 0, "state": 1, "type": 1})
    event = t.parse_notification_event(bytes.fromhex("0f02000101"))
    check("parse_notification_event publicEvent", event["event_name"], "publicEvent")
    check("parse_notification_event ai", event["public_event"]["ai"],
          {"state": 1, "type": 1})


def test_multi_connect_session() -> None:
    # the live-verified layout (Air7 Pro, 2026-10-20): 0x0112 -> [status][count]
    # + count x [mac:6][length][state][flag][nameLen][name]; the MAC is reversed.
    payload = bytes.fromhex(
        "00026655443322110c0201094c6170746f702d4100"
        "ffeeddccbbaa0b00000850686f6e652d4200")
    transport, session = replay([
        ("tx", wire(0x0112)), ("rx", wire(0x8112, payload)),
    ])
    result = session.read_multi_connect()
    check("multi_connect count", result["count"], 2)
    check("multi_connect entry 0 mac", result["entries"][0]["mac"], "11:22:33:44:55:66")
    check("multi_connect entry 0 name", result["entries"][0]["name"], "Laptop-A")
    check("multi_connect entry 0 state/flag",
          (result["entries"][0]["state"], result["entries"][0]["flag"]), (2, 1))
    check("multi_connect entry 1 mac", result["entries"][1]["mac"], "aa:bb:cc:dd:ee:ff")
    check("parse_multi_connect short", t.parse_multi_connect(b"\x00"),
          {"status": 0, "count": 0, "entries": []})


def test_personalized_anc_and_notification_caps() -> None:
    # the live-verified reads (Air7 Pro, 2026-10-20):
    #   0x011A -> 00 00 (no stored personalized-noise result)
    #   0x0200 -> 00 02 02 01 (push-event types 0x02 and 0x01)
    transport, session = replay([
        ("tx", wire(0x011A)), ("rx", wire(0x811A, bytes.fromhex("0000"))),
        ("tx", wire(0x0200)), ("rx", wire(0x8200, bytes.fromhex("00020201"))),
    ])
    check("read_personalized_anc", session.read_personalized_anc(),
          {"status": 0, "exists": 0})
    check("read_notification_capabilities", session.read_notification_capabilities(),
          {"status": 0, "count": 2, "types": [2, 1]})
    check("parse_personalized_anc short", t.parse_personalized_anc(b"\x00"),
          {"status": 0, "exists": None})
    check("parse_notification_capabilities short",
          t.parse_notification_capabilities(b"\x00"),
          {"status": 0, "count": 0, "types": []})
    check("decode_state 0x811A", t.decode_state(0x811A, bytes.fromhex("0000")),
          {"status": 0, "exists": 0})
    check("decode_state 0x8200", t.decode_state(0x8200, bytes.fromhex("00020201")),
          {"status": 0, "count": 2, "types": [2, 1]})


def test_identity_session() -> None:
    # the live-verified identity reads (Air7 Pro, 2026-10-20)
    transport, session = replay([
        ("tx", wire(0x0102)), ("rx", wire(0x8102, bytes.fromhex("002f07"))),
        ("tx", wire(0x0104)), ("rx", wire(0x8104, bytes.fromhex("00000001"))),
        ("tx", wire(0x0107)), ("rx", wire(0x8107, bytes.fromhex("00010103"))),
        ("tx", wire(0x010B)), ("rx", wire(0x810B, bytes.fromhex("0006"))),
    ])
    check("read_remote_vendor_id", session.read_remote_vendor_id(),
          {"status": 0, "vendor_id": 0x072F})
    check("read_protocol_version", session.read_protocol_version(),
          {"status": 0, "data": "000001"})
    check("read_upgrade_capability", session.read_upgrade_capability(),
          {"status": 0, "count": 1,
           "entries": [{"device_type": 1, "support_ota": True, "ota_type": 1}]})
    check("read_color_id", session.read_color_id(), {"status": 0, "color_id": 6})
    check("decode_state 0x8102", t.decode_state(0x8102, bytes.fromhex("002f07")),
          {"status": 0, "vendor_id": 0x072F})
    check("decode_state 0x810B", t.decode_state(0x810B, bytes.fromhex("0006")),
          {"status": 0, "color_id": 6})


def test_request_push_family() -> None:
    # device->app requests pushed in response to live writes (Air7 Pro,
    # 2026-10-20), replayed from the recorded fixture.
    transport = ReplayTransport.from_jsonl(ROOT / "fixtures" / "requests.jsonl", strict=True)
    transport.open("replay", 12)
    session = t.Session(transport, cmd_interval=0.0, read_timeout=0.5)
    session.request(0x0406, bytes([4]))
    session.request(0x0418, bytes([5]))
    session.request(0x041B, bytes.fromhex("fb0500"))
    packets = session.events + session.listen(0.2)  # drain the trailing 0x0508 push
    reqs = [p for p in packets if p["cmd"] in t.REQUEST_DECODERS]
    check("request push count", len(reqs), 3)
    check("0x0504 live", t.decode_request(0x0504, bytes(reqs[0]["payload"])),
          {"eq_mode": 4})
    check("0x0506 live", t.decode_request(0x0506, bytes(reqs[1]["payload"]))["entries"][0]["eq_id"], 4)
    check("0x0508 live", t.decode_request(0x0508, bytes(reqs[2]["payload"])),
          {"min": -5, "max": 5, "current": 0, "type": 0})


def test_compactness_session() -> None:
    # the live-verified trigger (Air7 Pro, 2026-10-20): 0x0405 01 -> ack, then a
    # 0x0204 event-0x04 push carries two [earSide][value] records.
    transport, session = replay([
        ("tx", wire(0x0405, bytes([1]))), ("rx", wire(0x8405, bytes([0]))),
        ("rx", wire(0x0204, bytes.fromhex("0401040204"))),
    ])
    session.start_compactness_detect()
    check("payload_compactness_detect(True)", t.payload_compactness_detect(True), b"\x01")
    check("payload_compactness_detect(False)", t.payload_compactness_detect(False), b"\x00")
    check("parse_compactness_event", t.parse_compactness_event(bytes.fromhex("01040204")),
          [{"side": 1, "side_name": "left", "value": 4},
           {"side": 2, "side_name": "right", "value": 4}])
    event = t.parse_notification_event(bytes.fromhex("0401040204"))
    check("parse_notification_event compactness event", event["event_name"], "compactness")
    check("parse_notification_event compactness records",
          event["compactness"][1]["side_name"], "right")
    check("parse_compactness_event short", t.parse_compactness_event(b"\x01\x04"), [])
    pushed = session.listen(0.1)
    check("compactness push observed",
          any(p["cmd"] == 0x0204 for p in pushed), True)


def test_cli_eq() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmpdir:
        rec = pathlib.Path(tmpdir) / "eq.jsonl"
        rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x010F)), ("rx", wire(0x810F, bytes.fromhex("0004"))),
            ("tx", wire(0x0122)), ("rx", wire(0x8122, bytes.fromhex("0001010102040000")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(rec), "eq", "get"])
        check("cli eq get rc", rc, 0)
        check("cli eq mode line", "eq mode   : 4" in buf.getvalue(), True)
        check("cli eq entry", "eqId=4" in buf.getvalue(), True)


def test_cli_high_volume() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmpdir:
        get_rec = pathlib.Path(tmpdir) / "hv-get.jsonl"
        get_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0110)), ("rx", wire(0x8110, bytes.fromhex("0001")))]), encoding="utf-8")
        off_rec = pathlib.Path(tmpdir) / "hv-off.jsonl"
        off_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0407, bytes([0]))), ("rx", wire(0x8407, bytes([0]))),
            ("tx", wire(0x0110)), ("rx", wire(0x8110, bytes.fromhex("0000")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(get_rec), "high-volume", "get"])
        check("cli high-volume get rc", rc, 0)
        check("cli high-volume get line", "high volume: on" in buf.getvalue(), True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(off_rec), "high-volume", "off"])
        check("cli high-volume off rc", rc, 0)
        check("cli high-volume off line",
              "wrote off, read back off" in buf.getvalue(), True)


def test_cli_find() -> None:
    from tlcli.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/find-mode.jsonl", "find", "--seconds", "0"])
    check("cli find rc", rc, 0)
    text = buf.getvalue()
    check("cli find says it rings", "the buds are ringing" in text, True)
    check("cli find stops", "find mode: off" in text, True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/find-mode.jsonl", "find", "--seconds", "0", "--json"])
    check("cli find --json rc", rc, 0)
    doc = json.loads(buf.getvalue())
    check("cli find --json payload",
          (doc["find_mode"], doc["stopped"]), (True, True))


def test_cli_ai_status() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmpdir:
        get_rec = pathlib.Path(tmpdir) / "ai-get.jsonl"
        get_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0131)), ("rx", wire(0x8131, bytes.fromhex("000101")))]), encoding="utf-8")
        set_rec = pathlib.Path(tmpdir) / "ai-set.jsonl"
        set_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0428, bytes([1, 1]))), ("rx", wire(0x8428, bytes([0]))),
            ("tx", wire(0x0131)), ("rx", wire(0x8131, bytes.fromhex("000101")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(get_rec), "ai-status", "get"])
        check("cli ai-status get rc", rc, 0)
        check("cli ai-status get line",
              "ai status : state=1 type=1" in buf.getvalue(), True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(set_rec), "ai-status", "set", "1", "1"])
        check("cli ai-status set rc", rc, 0)
        check("cli ai-status set line",
              "wrote state=1 type=1, read back state=1 type=1" in buf.getvalue(), True)


def test_cli_multipoint() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmpdir:
        rec = pathlib.Path(tmpdir) / "mp.jsonl"
        rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0112)), ("rx", wire(0x8112, bytes.fromhex(
                "00026655443322110c0201094c6170746f702d4100"
                "ffeeddccbbaa0b00000850686f6e652d4200")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(rec), "multipoint"])
        check("cli multipoint rc", rc, 0)
        check("cli multipoint count", "multipoint: 2 device(s)" in buf.getvalue(), True)
        check("cli multipoint entry", "11:22:33:44:55:66" in buf.getvalue(), True)


def test_cli_events_multi_connect() -> None:
    """The paired-list push (0x0204 event 0x06) decodes to its entries."""
    from tlcli.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/multi-connect-event.jsonl", "events",
                   "--seconds", "0.3"])
    check("cli events multiConnect rc", rc, 0)
    text = buf.getvalue()
    check("cli events multiConnect count", "multiConnect: 2 device(s)" in text, True)
    check("cli events multiConnect entry", "11:22:33:44:55:66" in text, True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/multi-connect-event.jsonl", "events",
                   "--seconds", "0.3", "--json"])
    check("cli events multiConnect --json rc", rc, 0)
    rows = [r for r in json.loads(buf.getvalue()) if "multi_connect" in r]
    check("cli events multiConnect --json entry",
          rows and rows[0]["multi_connect"]["entries"][1]["name"], "Phone-B")


def test_cli_personalized_anc_and_notification_caps() -> None:
    from tlcli.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/personalized-anc.jsonl", "personalized-noise"])
    check("cli personalized-noise rc", rc, 0)
    check("cli personalized-noise line",
          "personalized noise : result stored=False" in buf.getvalue(), True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/notification-caps.jsonl", "notification-caps"])
    check("cli notification-caps rc", rc, 0)
    check("cli notification-caps line",
          "notification capabilities: 2 type(s): 0x02, 0x01" in buf.getvalue(), True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/identity.jsonl", "identity"])
    check("cli identity rc", rc, 0)
    check("cli identity vendor", "vendor id : 0x072F" in buf.getvalue(), True)
    check("cli identity color", "color id  : 6" in buf.getvalue(), True)
    check("cli identity upgrade", "upgrade   : type=1 ota=True/1" in buf.getvalue(), True)


def test_cli_compactness() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmpdir:
        rec = pathlib.Path(tmpdir) / "cp.jsonl"
        rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
            ("tx", wire(0x0405, bytes([1]))), ("rx", wire(0x8405, bytes([0]))),
            ("rx", wire(0x0204, bytes.fromhex("0401040204")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(rec), "compactness", "start"])
        check("cli compactness start rc", rc, 0)
        check("cli compactness event line", "compactness: left=4, right=4" in buf.getvalue(), True)


def test_keys_session_and_cli() -> None:
    from tlcli.cli import main, parse_key_update
    from tlcore import decode_event

    kf_table = bytes.fromhex(
        "000a"
        "02010100" "02010201" "02010306" "02010408" "02010600"
        "01010100" "01010201" "01010305" "01010408" "01010600")
    caps = "00bf4f5ae665dc4004"

    # session: read -> patch -> whole-table write -> read back
    patched = t.patch_key_functions(
        t.parse_key_function_table(kf_table)["entries"], {(2, 1, 4): 4})
    written = t.payload_set_key_function([e.to_bytes() for e in patched])
    new_table = b"\x00\x0a" + written[1:]
    transport, session = replay([
        ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(caps))),
        ("tx", wire(0x0108)), ("rx", wire(0x8108, kf_table)),
        ("tx", wire(0x0401, written)), ("rx", wire(0x8401, b"\x00")),
        ("tx", wire(0x0108)), ("rx", wire(0x8108, new_table)),
    ])
    session.negotiate_capabilities()
    check("read_key_function count", session.read_key_function()["count"], 10)
    check("set_key_function status", session.set_key_function(patched), 0)
    check("read-back changed", session.read_key_function()["entries"][3],
          t.KeyFunctionInfo(2, 1, 4, 4))

    # parse_key_update accepts names and ints
    check("parse_key_update names", parse_key_update("right:1:long-press:noise-control"),
          ((2, 1, 4), 8))
    check("parse_key_update ints", parse_key_update("2:1:4:8"), ((2, 1, 4), 8))
    check("parse_key_update off", parse_key_update("1:1:5:off"), ((1, 1, 5), 0))
    check_raises("parse_key_update bad arity", SystemExit, parse_key_update, "2:1:4")

    # decode_event: button press, battery, and a device -> app request
    press = decode_event({"cmd": 0x0204, "payload": bytes.fromhex("f102010200000000")})
    check("event name", press["event"], "userInteraction")
    check("event press function", press["press"]["function_name"], "off")
    check("event press action", press["press"]["button_action"], 2)
    battery = decode_event({"cmd": 0x0204, "payload": bytes.fromhex("0103016402640300")})
    check("event battery tlv", battery["tlv"], {1: 100, 2: 100, 3: 0})
    req = decode_event({"cmd": 0x0504, "payload": bytes([4])})
    check("event request", req["request"], {"eq_mode": 4})

    with tempfile.TemporaryDirectory() as tmpdir:
        get_rec = pathlib.Path(tmpdir) / "keys-get.jsonl"
        get_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(caps))),
            ("tx", wire(0x0108)), ("rx", wire(0x8108, kf_table))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(get_rec), "keys", "get"])
        out = buf.getvalue()
        check("cli keys get rc", rc, 0)
        check("cli keys get count", "count=10" in out, True)
        check("cli keys get label", "right triple-click -> next-track" in out, True)

        buf = io.StringIO()
        rejected = False
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                main(["--replay", str(get_rec), "keys", "set", "9:9:9:0", "--confirm"])
            except SystemExit:
                rejected = True
        check("cli keys set rejects an unknown gesture", rejected, True)

        off = t.disable_long_press(t.parse_key_function_table(kf_table)["entries"])
        off_payload = t.payload_set_key_function([e.to_bytes() for e in off])
        off_table = b"\x00\x0a" + off_payload[1:]
        off_rec = pathlib.Path(tmpdir) / "keys-off.jsonl"
        off_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(caps))),
            ("tx", wire(0x0108)), ("rx", wire(0x8108, kf_table)),
            ("tx", wire(0x0401, off_payload)), ("rx", wire(0x8401, b"\x00")),
            ("tx", wire(0x0108)), ("rx", wire(0x8108, off_table))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["--replay", str(off_rec), "keys", "disable-long-press", "--confirm"])
        out = buf.getvalue()
        check("cli keys disable rc", rc, 0)
        check("cli keys disable wrote", "0x8401 status=0" in out, True)
        check("cli keys disable off", "2:1:4:0" in out, True)

        ev_rec = pathlib.Path(tmpdir) / "events.jsonl"
        ev_rec.write_text("\n".join(json.dumps({"dir": d, "hex": b.hex()}) for d, b in [
            ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex(caps))),
            ("rx", wire(0x0204, bytes.fromhex("f102010200000000")))]), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["--replay", str(ev_rec), "events", "--seconds", "0.1"])
        out = buf.getvalue()
        check("cli events rc", rc, 0)
        check("cli events press", "button press: off" in out, True)


def test_not_worn_and_rejection() -> None:
    transport, session = replay([
        ("tx", wire(0x0404, bytes([1, 1, 1]))),
        ("rx", wire(0x8404, bytes([0x0E]))),
    ])
    check_raises("not-worn is NotWorn", NotWorn,
                 session.set_noise_mode, t.NOISE_MODE_NORMAL)
    check("NotWorn is a DeviceRejected", issubclass(NotWorn, DeviceRejected), True)

    transport, session = replay([
        ("tx", wire(0x0404, bytes([1, 1, 1]))),
        ("rx", wire(0x8404, bytes([0x01]))),
    ])
    check_raises("status 1 is DeviceRejected", DeviceRejected,
                 session.set_noise_mode, t.NOISE_MODE_NORMAL)


def test_timeout() -> None:
    transport, session = replay([("tx", wire(0x0100))])
    check_raises("no reply times out", Timeout, session.request, 0x0100, timeout=0.05)


def test_cli_bass() -> None:
    """``tlcli bass`` over a replay of the live ``0x012C`` exchange."""
    from tlcli.cli import main

    records = [
        ("tx", wire(0x0100)),
        ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
        ("tx", wire(0x012C)),
        ("rx", wire(0x812C, bytes.fromhex("0003fb050001fb050002fb050003"))),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "bass.jsonl"
        path.write_text("\n".join(
            json.dumps({"ts": 0.0, "dir": d, "hex": h.hex()}) for d, h in records) + "\n", encoding="utf-8")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", str(path), "bass"])
        out = buf.getvalue()
        check("exit code", rc, 0)
        check("band count", "3 band(s)" in out, True)
        # signed values, and a label per band type
        check("signed range", "range -5..+5" in out, True)
        check("band type 1", "type 1" in out, True)
        check("band type 3", "type 3" in out, True)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["--replay", str(path), "--json", "bass"])
        parsed = json.loads(buf.getvalue())
        check("json count", parsed["count"], 3)
        check("json entries", parsed["entries"][2],
              {"min": -5, "max": 5, "current": 0, "type": 3})


def test_cli_replay() -> None:
    from tlcli.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/spp-smoketest.jsonl", "info"])
    out = buf.getvalue()
    check("info exit code", rc, 0)
    check("info shows the model", "0x064C12" in out, True)
    check("info shows the mode", "Normal" in out, True)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/mode-cycle.jsonl", "mode", "get"])
    check("mode get exit code", rc, 0)
    check("mode get output", "0x01" in buf.getvalue(), True)

    # the hearing/ear-scan family is refused without --force
    message = ""
    try:
        main(["--replay", "fixtures/mode-cycle.jsonl", "send", "0x0415"])
    except SystemExit as exc:
        message = str(exc)
    check("ear-scan is refused", "hearing" in message.lower(), True)

    # the OTA and debug families are refused too (both deliberately unimplemented)
    for cmd, needle in (("0x0302", "ota"), ("0x0f00", "debug")):
        message = ""
        try:
            main(["--replay", "fixtures/mode-cycle.jsonl", "send", cmd])
        except SystemExit as exc:
            message = str(exc)
        check(f"{cmd} is refused", needle in message.lower(), True)

    # the guard table is the core's, and `send` refuses EVERY id in it -- not
    # just the three sampled above, so a new family cannot be added half-wired
    from tlcli.cli import DANGEROUS
    check("the CLI guard is the core's table", DANGEROUS, t.DANGEROUS_COMMANDS)
    check("the guard covers three families",
          len(set(t.DANGEROUS_COMMANDS.values())), 3)
    for cmd in t.DANGEROUS_COMMANDS:
        message = ""
        try:
            main(["--replay", "fixtures/mode-cycle.jsonl", "send", f"0x{cmd:04X}"])
        except SystemExit as exc:
            message = str(exc)
        check(f"0x{cmd:04X} is refused by send",
              t.dangerous_family(cmd) in message, True)


def test_replay_edges() -> None:
    from tlcore.errors import NotConnected
    from tltransport.replay import ReplayError

    tr = ReplayTransport([("tx", b"\xaa")])
    check("read before open", tr.read(0.0), b"")
    check_raises("write before open", NotConnected, tr.write, b"\xaa")

    tr = ReplayTransport([("tx", wire(0x0100))])
    tr.open("replay", 12)
    tr.write(wire(0x0100))
    check_raises("write past end is strict", ReplayError, tr.write, wire(0x0100))

    tr = ReplayTransport([("tx", wire(0x0100))], loop=True)
    tr.open("replay", 12)
    tr.write(wire(0x0100))
    tr.write(wire(0x0100))
    check("loop wraps around", len(tr.writes), 2)

    tr = ReplayTransport([("rx", wire(0x8100))], strict=False)
    tr.open("replay", 12)
    tr.write(b"\x00")
    check("loose write skips an rx record", len(tr.writes), 1)
    check("repr names the source", "inline" in repr(tr), True)

    tr = ReplayTransport([("tx", wire(0x0100))], strict=False)
    tr.open("replay", 12)
    tr.write(wire(0x0100))
    tr.write(b"\x00")  # past the end, loose
    check("loose write past end is accepted", len(tr.writes), 2)


def test_unsolicited_buffering() -> None:
    transport, session = replay([
        ("rx", wire(0x0204, bytes([1, 3, 1, 100, 2, 100, 3, 0]))),
        ("tx", wire(0x0100)),
        ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
    ])
    caps = session.negotiate_capabilities()
    check("caps parsed despite a leading push", caps["ok"], True)
    check("leading push captured", any(p["cmd"] == 0x0204 for p in session.events), True)


def test_session_edges() -> None:
    from tlcore.errors import NotConnected, PacketError

    session = t.Session(ReplayTransport([]), cmd_interval=0.0)
    check("always-on before negotiating", session.supports(0x0100), True)
    check("unknown before negotiating", session.supports(0x0404), False)
    check_raises("send while disconnected", NotConnected, session.send, 0x0100)
    check("status_of an empty payload", t.Session.status_of({"payload": b""}), 0)

    transport, session = replay([("tx", wire(0x010C, bytes([1]))),
                                 ("rx", wire(0x810C, bytes([0])))])
    check_raises("short 0x810C body", PacketError, session.read_noise_mode)

    transport, session = replay([("rx", wire(0x0204, bytes([3, 1, 1, 4])))])
    packets = session.listen(0.05)
    check("listen collects packets", any(p["cmd"] == 0x0204 for p in packets), True)


def _fixture_file(directory: str, records) -> str:
    path = pathlib.Path(directory) / "fixture.jsonl"
    path.write_text("".join(
        json.dumps({"ts": i, "dir": d, "hex": b.hex()}) + "\n"
        for i, (d, b) in enumerate(records)), encoding="utf-8")
    return str(path)


def test_cli_edges() -> None:
    from tlcli.cli import main, parse_cmd, parse_mode

    check("parse_mode name", parse_mode("anc"), t.NOISE_MODE_ANC)
    check("parse_mode hex", parse_mode("0x08"), t.NOISE_MODE_ANC_DEEP)
    check("parse_mode adaptive", parse_mode("adaptive"), t.NOISE_MODE_ANC_SMART)
    check_raises("parse_mode rejects junk", SystemExit, parse_mode, "nope")
    check("parse_cmd hex", parse_cmd("0x0404"), 0x0404)
    check_raises("parse_cmd rejects junk", SystemExit, parse_cmd, "zzz")

    with tempfile.TemporaryDirectory() as tmp:
        set_path = _fixture_file(tmp, [
            ("tx", wire(0x0404, bytes([1, 1, 0x20]))),
            ("rx", wire(0x8404, b"\x00")),
            ("tx", wire(0x010C, bytes([1]))),
            ("rx", wire(0x810C, bytes([0, 1, 1, 0x20]))),
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", set_path, "mode", "set", "anc"])
        check("mode set exit code", rc, 0)
        check("mode set output", "0x20" in buf.getvalue(), True)

        get_path = _fixture_file(tmp, [
            ("tx", wire(0x010C, bytes([1]))),
            ("rx", wire(0x810C, bytes([0, 1, 1, 2]))),
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", get_path, "mode", "get", "--json"])
        check("mode get --json exit code", rc, 0)
        check("mode get --json is parseable",
              json.loads(buf.getvalue())["noise_mode"], 0x02)

        not_worn_path = _fixture_file(tmp, [
            ("tx", wire(0x0404, bytes([1, 1, 1]))),
            ("rx", wire(0x8404, b"\x0e")),
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["--replay", not_worn_path, "mode", "set", "normal"])
        check("not-worn exit code", rc, 2)

        force_path = _fixture_file(tmp, [
            ("tx", wire(0x0415)),
            ("rx", wire(0x8415, b"\x00")),
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", force_path, "send", "0x0415", "--force",
                       "--read", "0.02"])
        check("forced ear-scan send exit code", rc, 0)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/spp-smoketest.jsonl", "caps"])
    check("caps exit code", rc, 0)
    check("caps output", "capability report" in buf.getvalue(), True)

    check_raises("services refuses under replay", SystemExit, main,
                 ["--replay", "fixtures/mode-cycle.jsonl", "services"])
    check_raises("live mode needs an address", SystemExit, main, ["caps"])


def test_stream_decoder_edges() -> None:
    # junk before a frame is surfaced, not silently dropped
    events = t.StreamDecoder().feed(b"\x00\x01\x02" + wire(0x0204, bytes([3, 1, 1, 4])))
    check("junk precedes the packet", events[0]["kind"], "junk")
    check("packet follows the junk", events[1]["packet"]["cmd"], 0x0204)

    # a frame split across two reads
    data = wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))
    decoder = t.StreamDecoder()
    check("partial frame yields nothing", decoder.feed(data[:4]), [])
    check("pending holds the partial", decoder.pending() > 0, True)
    check("completed frame decodes", decoder.feed(data[4:])[-1]["packet"]["cmd"], 0x8100)

    # leftover bytes flush as junk
    decoder = t.StreamDecoder()
    decoder.feed(b"\xaa\x08")
    flushed = decoder.flush()
    check("flush emits junk", bool(flushed) and flushed[0]["kind"], "junk")
    check("flush empties the buffer", decoder.flush(), [])

    # raw mode: length-prefixed packets with no OPPOv1 framing
    raw = t.build_packet(0x0404, bytes([1, 1, 1]))
    events = t.StreamDecoder(raw=True).feed(b"\xff" + raw)
    check("raw junk prefix", events[0]["kind"], "junk")
    check("raw packet decodes", events[1]["packet"]["cmd"], 0x0404)


def test_decoder_edges() -> None:
    # 0x812A / parse_feature_detect was removed: the unit never answers 0x012A.
    check("feature_detect not implemented", hasattr(t, "parse_feature_detect"), False)
    check("pairs on empty input", t.parse_pairs(b""), (0, []))
    check("use_time too short", t.parse_use_time(b"\x00")["devices"], {})
    check("model_id too short", t.parse_model_id(b"\x00")["model_id"], None)
    check("eq_info too short", t.parse_eq_info(b"\x00")["entries"], [])
    eq = t.parse_eq_info(bytes.fromhex("0001010102040000"))
    check("eq_info count", eq["count"], 1)
    check("eq_info entry", eq["entries"][0],
          {"is_selected": 1, "min": 1, "max": 2, "eq_id": 4, "name": "", "points": []})
    # custom curve: little-endian freq, signed int8 db (matches payload_set_eq)
    curve = t.parse_eq_info(bytes.fromhex("0001010102040001" + "2a01" + "fb"))
    check("eq_info curve point", curve["entries"][0]["points"],
          [{"freq": 0x012A, "db": -5}])
    check("eq_mode decode", t.parse_eq_mode(bytes.fromhex("0004")), {"status": 0, "mode": 4})
    check("eq_mode too short", t.parse_eq_mode(b"")["mode"], None)
    check("payload_set_eq_mode", t.payload_set_eq_mode(4), b"\x04")
    check("decode_state dispatch",
          t.decode_state(0x8106, bytes.fromhex("0003016402640300"))["devices"]["L"]["level"], 100)
    check("decode_state unknown id", t.decode_state(0x9999, b""), None)
    check("empty notification", t.parse_notification_event(b"")["event_name"], "empty")
    check("noise notification",
          t.parse_notification_event(bytes.fromhex("03010101"))["event_name"], "noiseReduction")


def test_transport_factory() -> None:
    import tltransport
    from tltransport.replay import ReplayTransport as RT

    check("for_platform('replay')", tltransport.for_platform("replay"), RT)
    check("replay is available", "replay" in tltransport.available_transports(), True)
    check_raises("unknown transport rejected", ValueError, tltransport.for_platform, "nope")

    # `auto` means "the live transport for this platform", and only macOS has one
    if sys.platform == "darwin":
        check("platform_transport", tltransport.platform_transport(), "macos")
        check("for_platform() resolves here", tltransport.for_platform(),
              tltransport.for_platform("macos"))
        check("macos is available", "macos" in tltransport.available_transports(), True)
    elif sys.platform == "win32":
        check("platform_transport", tltransport.platform_transport(), "windows")
        check("for_platform() resolves here", tltransport.for_platform(),
              tltransport.for_platform("windows"))
        check("windows is available", "windows" in tltransport.available_transports(), True)
    else:
        check("no live transport here", tltransport.platform_transport(), None)
        check("macos is not advertised",
              "macos" in tltransport.available_transports(), False)
        check_raises("auto refused here", t.TransportUnavailable,
                     tltransport.for_platform)

    transport = tltransport.open_transport("x", name="replay", channel=12)
    check("open_transport returns replay", isinstance(transport, RT), True)
    check("open_transport is connected", transport.is_connected(), True)
    transport.close()
    check("close disconnects", transport.is_connected(), False)


def test_no_live_transport_off_macos() -> None:
    """`auto` must not fall back to replay on a platform with no radio.

    Regression: it used to resolve to ``replay``, so ``tlcli info --address X``
    on Linux died with ``write past the end of the recording`` -- naming a
    recording the user never asked for and saying nothing about the platform.
    The check runs on every host by faking ``sys.platform``, because the machine
    that develops this is the one that would never notice.
    """
    import tltransport
    from tltransport.replay import ReplayTransport as RT

    saved = sys.platform
    try:
        for fake in ("linux", "freebsd"):
            sys.platform = fake
            check(f"{fake}: no live transport", tltransport.platform_transport(), None)
            check(f"{fake}: only replay", tltransport.available_transports(), ["replay"])
            check_raises(f"{fake}: auto refused", t.TransportUnavailable,
                         tltransport.for_platform)
            check_raises(f"{fake}: auto refused (explicit)", t.TransportUnavailable,
                         tltransport.for_platform, "auto")
            # `--transport macos` on the wrong host is a platform error, not an
            # ImportError about a pyobjc dependency that reads like a fixable gap
            check_raises(f"{fake}: macos refused", t.TransportUnavailable,
                         tltransport.for_platform, "macos")
            check_raises(f"{fake}: windows refused", t.TransportUnavailable,
                         tltransport.for_platform, "windows")
            check(f"{fake}: replay still resolves", tltransport.for_platform("replay"), RT)
            # raised before any I/O, so the caller never sees a decode failure
            check_raises(f"{fake}: open_transport refused", t.TransportUnavailable,
                         tltransport.open_transport, "AA-BB-CC-DD-EE-FF", channel=12)

        # Windows now has a live transport, so `auto` must resolve to it -- and
        # must not be reachable from any other platform.
        sys.platform = "win32"
        check("win32: live transport", tltransport.platform_transport(), "windows")
        check("win32: both transports", tltransport.available_transports(),
              ["replay", "windows"])
        check("win32: for_platform() resolves",
              tltransport.for_platform("windows").__name__, "WindowsTransport")
        check_raises("win32: macos refused", t.TransportUnavailable,
                     tltransport.for_platform, "macos")
    finally:
        sys.platform = saved


def test_discovery_miss_is_a_tlerror() -> None:
    """A channel-discovery miss must be a ``TLError``, not a bare ``ValueError``.

    Regression: ``open_transport`` raised ``ValueError`` when discovery found
    nothing, which the front ends do not catch -- a bogus address printed a
    traceback instead of the one-line "pass --channel N" hint.
    """
    import tltransport

    class _NoChannel:
        def __init__(self, **_kwargs):
            pass

        def discover_channel(self, address, uuid):
            return None

        def open(self, address, channel):
            raise AssertionError("open must not be reached")

    saved = tltransport.for_platform
    tltransport.for_platform = lambda name=None: _NoChannel
    try:
        check_raises("discovery miss is a TLError", t.TransportError,
                     tltransport.open_transport, "AA-BB-CC-DD-EE-FF")
    finally:
        tltransport.for_platform = saved


def test_off_macos_errors_are_one_sentence() -> None:
    """Both front ends must explain, not traceback."""
    import contextlib
    import io

    from tlapp.controller import ConnectionOptions, ControllerError, DeviceController
    from tlcli.cli import main

    saved = sys.platform
    try:
        sys.platform = "linux"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = main(["info", "--address", "AA-BB-CC-DD-EE-FF"])
        check("cli rc", rc, 1)
        check("cli says which platform", "linux" in err.getvalue(), True)
        check("cli names the way out", "--replay" in err.getvalue(), True)
        check("cli did not traceback", "Traceback" in err.getvalue(), False)

        ctrl = DeviceController(ConnectionOptions(address="AA-BB-CC-DD-EE-FF"))
        check_raises("app connect refused", ControllerError, ctrl.connect)
        try:
            ctrl.connect()
        except ControllerError as exc:
            check("app message names the platform", "linux" in str(exc), True)
            check("app message names the way out", "--replay" in str(exc), True)

        # every macOS-only path says so, including the one that used to fail
        # with an OSError about a missing 'say' executable
        for argv in (["services", "--address", "AA-BB-CC-DD-EE-FF"],
                     ["say", "--list-voices"], ["say", "hello"],
                     ["say", "--out", "/tmp/x.aiff", "hi"]):
            try:
                main(argv)
                FAILURES.append(f"{' '.join(argv)}: expected SystemExit off macOS")
            except SystemExit as exc:
                check(f"{' '.join(argv)} names macOS", "macOS" in str(exc), True)
    finally:
        sys.platform = saved


def test_cli_more_errors() -> None:
    from tlcli.cli import main, parse_cmd

    check_raises("parse_cmd out of range", SystemExit, parse_cmd, "0x10000")
    check_raises("missing fixture", SystemExit, main, ["--replay", "nope.jsonl", "info"])
    check_raises("replay without a file", SystemExit, main, ["--transport", "replay", "caps"])


def test_multiframe_reassembly() -> None:
    # a message that needs several transport frames must reassemble to one packet
    payload = bytes(range(256)) * 2  # 512 bytes
    frames = t.build_frames(t.build_packet(0x0418, payload), 64)
    check("produced multiple frames", len(frames) > 1, True)

    decoder = t.StreamDecoder()
    events = decoder.feed(b"".join(frames))
    packets = [e["packet"] for e in events if e.get("kind") == "packet"]
    check("one packet from many frames", len(packets), 1)
    check("payload survives reassembly", packets[0]["payload"], payload)
    check("frame count is reported", packets and events[-1]["frames"] > 1, True)

    # and byte-by-byte, as RFCOMM may deliver it
    decoder = t.StreamDecoder()
    got = []
    for byte in b"".join(frames):
        got += [e["packet"] for e in decoder.feed(bytes([byte])) if e.get("kind") == "packet"]
    check("byte-wise reassembly", [p["payload"] for p in got], [payload])


def test_frame_payload_too_short_is_junk() -> None:
    # a well-formed transport frame whose packet header is truncated
    frame = bytes([t.SOF, 0x04, 0x00, 0x00, 0x04, 0x04])
    events = t.StreamDecoder().feed(frame)
    check("short packet body is junk", events[0]["kind"], "junk")
    check("junk carries the error", "error" in events[0], True)


def test_cli_json_and_errors() -> None:
    from tlcli.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["--replay", "fixtures/spp-smoketest.jsonl", "caps", "--json"])
    check("caps --json exit code", rc, 0)
    check("caps --json is parseable",
          len(json.loads(buf.getvalue())["commands"]), 49)

    with tempfile.TemporaryDirectory() as tmp:
        send_path = _fixture_file(tmp, [
            ("tx", wire(0x0415)),
            ("rx", wire(0x8415, b"\x00")),
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", send_path, "send", "0x0415", "--force",
                       "--read", "0.02", "--json"])
        check("send --json exit code", rc, 0)
        check("send --json reports frames", len(json.loads(buf.getvalue())["frames"]), 1)

        # a reply that never comes must surface as a timeout (exit 3)
        no_reply = _fixture_file(tmp, [("tx", wire(0x010C, bytes([1])))])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["--replay", no_reply, "mode", "get", "--timeout", "0.05"])
        check("timeout exit code", rc, 3)

        # a strict write mismatch must surface as an error (exit 1)
        mismatch = _fixture_file(tmp, [("tx", wire(0x0404, bytes([1, 1, 1])))])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(["--replay", mismatch, "mode", "get"])
        check("mismatch exit code", rc, 1)


def _status_records(mode: int = 0x01):
    """A synthetic recording of the exact poll sequence ``smoke`` performs."""
    return [
        ("tx", wire(0x0100)), ("rx", wire(0x8100, bytes.fromhex("00bf4f5ae665dc4004"))),
        ("tx", wire(0x0101)), ("rx", wire(0x8101, bytes.fromhex("00c008"))),
        ("tx", wire(0x0103)), ("rx", wire(0x8103, bytes.fromhex("00124c06"))),
        ("tx", wire(0x0105)), ("rx", wire(0x8105, b"\x00" + b"1.1.0.85")),
        ("tx", wire(0x0106)), ("rx", wire(0x8106, bytes.fromhex("0003016402640300"))),
        ("tx", wire(0x0109)), ("rx", wire(0x8109, bytes.fromhex("0003010302030304"))),
        ("tx", wire(0x010C, bytes([1]))), ("rx", wire(0x810C, bytes([0, 1, 1, mode]))),
        ("tx", wire(0x0128)),
        ("rx", wire(0x8128, bytes.fromhex("000301cfe0000002ade100000300000000"))),
    ]


def test_cli_smoke() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmp:
        read_path = _fixture_file(tmp, _status_records(0x01))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", read_path, "smoke"])
        out = buf.getvalue()
        check("smoke read-only exit code", rc, 0)
        check("smoke reports OK", "RESULT: OK" in out, True)
        check("smoke lists the capability count", "49 commands" in out, True)
        check("smoke shows the model", "0x064C12" in out, True)

        write_records = _status_records(0x01) + [
            ("tx", wire(0x010C, bytes([1]))), ("rx", wire(0x810C, bytes([0, 1, 1, 0x01]))),
            ("tx", wire(0x0404, bytes([1, 1, 0x20]))), ("rx", wire(0x8404, b"\x00")),
            ("tx", wire(0x010C, bytes([1]))), ("rx", wire(0x810C, bytes([0, 1, 1, 0x20]))),
            ("tx", wire(0x0404, bytes([1, 1, 0x01]))), ("rx", wire(0x8404, b"\x00")),
        ]
        write_path = _fixture_file(tmp, write_records)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", write_path, "smoke", "--write", "anc"])
        out = buf.getvalue()
        check("smoke --write exit code", rc, 0)
        check("smoke --write reports OK", "RESULT: OK" in out, True)
        check("smoke --write shows the write", "0x20" in out, True)
        check("smoke --write restores", "restore" in out, True)


def _cycle_records(readbacks=None, original: int = 0x01):
    """A synthetic recording of a full ``mode cycle`` (set + read-back each)."""
    order = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20]
    readbacks = readbacks or order
    recs = [("tx", wire(0x010C, bytes([1]))),
            ("rx", wire(0x810C, bytes([0, 1, 1, original])))]
    for value, read_back in zip(order, readbacks):
        recs += [("tx", wire(0x0404, bytes([1, 1, value]))),
                 ("rx", wire(0x8404, b"\x00")),
                 ("tx", wire(0x010C, bytes([1]))),
                 ("rx", wire(0x810C, bytes([0, 1, 1, read_back])))]
    recs += [("tx", wire(0x0404, bytes([1, 1, original]))),
             ("rx", wire(0x8404, b"\x00"))]
    return recs


def test_cli_mode_cycle() -> None:
    from tlcli.cli import main

    with tempfile.TemporaryDirectory() as tmp:
        path = _fixture_file(tmp, _cycle_records())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", path, "mode", "cycle", "--dwell", "0"])
        out = buf.getvalue()
        check("mode cycle exit code", rc, 0)
        check("mode cycle reports OK", "RESULT: OK" in out, True)
        check("mode cycle checks all six", out.count("[PASS]"), 6)
        check("mode cycle restores the original", "restored" in out, True)

        # a wrong read-back must be detected and fail the run
        bad = _cycle_records(readbacks=[0x01, 0x02, 0x04, 0x08, 0x10, 0x10])
        bad_path = _fixture_file(tmp, bad)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--replay", bad_path, "mode", "cycle", "--dwell", "0"])
        check("mode cycle detects a mismatch", rc, 1)
        check("mode cycle reports FAIL", "RESULT: FAIL" in buf.getvalue(), True)


def test_cli_say() -> None:
    from tlcli.cli import main, say_command, status_sentence

    check("say plain", say_command("hello", voice=None, rate=None, out=None),
          ["say", "hello"])
    check("say out/voice/rate",
          say_command("hi", voice="Alex", rate=180, out="/tmp/x.aiff"),
          ["say", "-o", "/tmp/x.aiff", "-v", "Alex", "-r", "180", "hi"])
    check("say rate truncates",
          say_command("x", voice=None, rate=180.7, out=None), ["say", "-r", "180", "x"])

    check("status sentence", status_sentence({
        "model": {"model_id": 0x064C12},
        "firmware": {"versions": ["1.1.0.85"]},
        "battery": {"devices": {"L": {"level": 100}, "R": {"level": 100},
                                 "CASE": {"level": 0}}},
        "wear": {"devices": {"L": 3, "R": 3, "CASE": 4}},
        "noise_mode": 0x01,
        "use_time": {"devices": {"L": 3600, "R": 3900}},
    }), "realme Buds Air7 Pro, firmware 1.1.0.85, battery 100 percent left and "
        "100 percent right, in ear, noise mode normal, use time 1 hour left "
        "and 1 hour 5 minutes right.")
    check("status sentence skips unknowns", status_sentence({}), "the headset.")

    # a real synthesis with --out writes an audio file (no sound is played)
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "cue.aiff"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = main(["say", "--out", str(out), "hello"])
            check("cli say --out rc", rc, 0)
            check("cli say --out file", out.exists() and out.stat().st_size > 0, True)

            # --status reads a replay fixture and speaks the summary to a file
            replay = _fixture_file(tmp, _status_records(0x01))
            out2 = pathlib.Path(tmp) / "status.aiff"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = main(["--replay", replay, "say", "--status", "--out", str(out2)])
            check("cli say --status rc", rc, 0)
            check("cli say --status names the model",
                  "realme Buds Air7 Pro" in buf.getvalue(), True)
            check("cli say --status wrote audio",
                  out2.exists() and out2.stat().st_size > 0, True)


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
    print("TRANSPORT/SESSION: OK")
    return 0


def test_transport_and_session() -> None:
    """pytest entry point.

    The per-test functions above only *record* failures in ``FAILURES``; pytest
    would happily pass a file full of them.  This is the only test that turns
    them into a result, so it must stay last and must never be renamed to
    something ``main()`` would call back into.
    """
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
