#!/usr/bin/env python3
"""Byte-for-byte parity between ``tlcore`` and an external reference codec.

``tlcore`` was ported from a reference codec that was validated against a real
Buds Air7 Pro over live SPP.  A port that merely *looks* right is worthless:
this harness is the proof that it still produces identical bytes, on real
recorded traffic as well as synthetic input.

The reference codec is not part of this repository; point the harness at it with
``TL_REFERENCE``.  This is the only file permitted to import both.  Once the
port is trusted it can be frozen as a historical check; until then it is the
gate.

    TL_REFERENCE=/path/to/tl_frame.py python3 tests/test_parity_tl_frame.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The reference codec is supplied out of tree via TL_REFERENCE; the repository
# does not vendor it or record where it lives.
_reference = os.environ.get("TL_REFERENCE")
REFERENCE_PATH = pathlib.Path(_reference) if _reference else None
FIXTURES = ROOT / "fixtures"

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def check_raises(label: str, fn, *args) -> None:
    """Both implementations must reject the same input."""
    global CHECKS
    CHECKS += 1
    try:
        fn(*args)
    except ValueError:
        return
    except Exception as exc:  # pragma: no cover - diagnostic
        FAILURES.append(f"{label}: raised {type(exc).__name__}, expected ValueError")
        return
    FAILURES.append(f"{label}: accepted input that should have been rejected")


def load_reference(path: pathlib.Path):
    if not path.exists():
        raise SystemExit(
            f"reference codec not found: {path}\n"
            "set TL_REFERENCE=/path/to/tl_frame.py"
        )
    spec = importlib.util.spec_from_file_location("tl_frame_reference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_fixture_hexes(name: str) -> list[tuple[str, str]]:
    """Read a recorded exchange -> [(direction, hex), ...]."""
    path = FIXTURES / name
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rows.append((rec["dir"], rec["hex"]))
    return rows


# --------------------------------------------------------------------------
# 1. constants
# --------------------------------------------------------------------------
def parity_constants(ref, core) -> None:
    for name in ("CLASS_MASK", "RESPONSE_BIT", "SOF"):
        check(f"constant {name}", getattr(core, name), getattr(ref, name))

    # every CLASS_*, CMD_*, NOISE_* and *_FIELD_* symbol, discovered by name so
    # that a symbol added to the reference but not the port shows up as a miss
    for name in sorted(dir(ref)):
        if not name.isupper():
            continue
        if not name.startswith(("CLASS_", "CMD_", "NOISE_")):
            continue
        if not hasattr(core, name):
            FAILURES.append(f"missing symbol in tlcore: {name}")
            global CHECKS
            CHECKS += 1
            continue
        check(f"constant {name}", getattr(core, name), getattr(ref, name))

    check("CAPABILITY_TABLE", core.CAPABILITY_TABLE, ref.CAPABILITY_TABLE)
    check("CAPABILITY_TABLE length", len(core.CAPABILITY_TABLE), 61)
    check("ALWAYS_ON", core.ALWAYS_ON, ref.ALWAYS_ON)
    check("CMD_NAMES", core.CMD_NAMES, ref.CMD_NAMES)
    check("ALL_COMMANDS", core.ALL_COMMANDS, ref.ALL_COMMANDS)
    check("COMMAND_IDS", core.COMMAND_IDS, ref.COMMAND_IDS)
    check("NOISE_MODE_NAMES", core.NOISE_MODE_NAMES, ref.NOISE_MODE_NAMES)
    check("EVENT_TYPE_NAMES", core.EVENT_TYPE_NAMES, ref.EVENT_TYPE_NAMES)


# --------------------------------------------------------------------------
# 2. packet header
# --------------------------------------------------------------------------
def parity_packet(ref, core) -> None:
    cases = [
        (0x0404, b"\x01\x01\x01", 0),
        (0x010C, b"\x01\x01", 0),
        (0x0100, b"", 0),
        (0x0204, b"\x03\x01\x01\x08", 0x1B),
        (0x8404, b"\x00", 0),
        (0xFFFF, bytes(range(256)), 0xFF),
        (0x0000, b"", 0),
    ]
    for cmd, payload, type_byte in cases:
        label = f"build_packet(0x{cmd:04X}, {len(payload)}B, t={type_byte})"
        want = ref.build_packet(cmd, payload, type_byte)
        check(label, core.build_packet(cmd, payload, type_byte), want)
        check(f"{label} round-trip", core.parse_packet(want), ref.parse_packet(want))
        check(f"{label} parse identity", core.parse_packet(want), ref.parse_packet(want))

    for short in (b"", b"\xaa", b"\xaa\x04\x00\x00"):
        check_raises(f"parse_packet({short!r})", core.parse_packet, short)
        check_raises(f"ref parse_packet({short!r})", ref.parse_packet, short)

    # parse_packet must stay a ValueError contract
    try:
        core.parse_packet(b"")
    except ValueError:
        check("parse_packet raises ValueError subclass", True, True)
    else:  # pragma: no cover
        check("parse_packet raises ValueError subclass", False, True)


# --------------------------------------------------------------------------
# 3. varint
# --------------------------------------------------------------------------
def parity_varint(ref, core) -> None:
    for value in list(range(0, 300)) + [1000, 16383, 16384, 65535, 1 << 20]:
        want = ref.varint_encode(value)
        check(f"varint_encode({value})", core.varint_encode(value), want)
        check(f"varint_decode({want.hex()})", core.varint_decode(want, 0),
              ref.varint_decode(want, 0))
        # offset handling
        check(f"varint_decode offset({value})",
              core.varint_decode(b"\xff\xff" + want, 2),
              ref.varint_decode(b"\xff\xff" + want, 2))


# --------------------------------------------------------------------------
# 4. framing  (the layer where a subtle port bug would be invisible)
# --------------------------------------------------------------------------
def parity_framing(ref, core) -> None:
    """Framing is where a subtle port bug hides, so this fuzzes hard.

    Note the FSN is a *single byte*, so a message needing more than 255 frames
    cannot be encoded at all -- ``build_frames`` raises rather than wrapping.
    That is a real protocol limit, not a porting artifact, and the harness
    asserts both implementations refuse identically.
    """
    rng = random.Random(0xA17E)

    # frame budgets chosen around the varint boundaries (129, 125, 124) and the
    # 512 default the Air7 Pro actually uses
    frame_sizes = [8, 16, 32, 129, 130, 200, 255, 512, 1000]
    payload_sizes = list(range(0, 40)) + [63, 64, 65, 124, 125, 126, 127, 128,
                                          129, 130, 200, 255, 256, 500, 511,
                                          512, 513, 1000, 2000, 4096]

    for fs in frame_sizes:
        for n in payload_sizes:
            data = bytes(rng.randrange(256) for _ in range(n))
            for flag_a in (False, True):
                for flag_b in (False, True):
                    label = f"build_frames({n}B, mtu={fs}, a={flag_a}, b={flag_b})"
                    try:
                        want = ref.build_frames(data, fs, flag_a, flag_b)
                    except ValueError:
                        check_raises(f"{label} FSN overflow", core.build_frames,
                                     data, fs, flag_a, flag_b)
                        continue
                    got = core.build_frames(data, fs, flag_a, flag_b)
                    check(label, got, want)
                    if want:
                        check(f"{label} frames<=mtu", all(len(f) <= fs for f in got), True)
                        check(f"{label} deframe", core.deframe(got), ref.deframe(want))
                        # Round-trip is NOT a property the reference satisfies at
                        # every MTU -- see check_known_frame_defect.  Where it
                        # holds, assert it; where it does not, assert the port
                        # reproduces the defect rather than silently diverging.
                        ref_round_trip = ref.deframe(want)
                        if ref_round_trip == data:
                            check(f"{label} round-trip", core.deframe(got), data)
                        else:
                            check(f"{label} reproduces reference defect",
                                  core.deframe(got), ref_round_trip)
                    for frame in want:
                        check(f"{label} parse_frame", core.parse_frame(frame),
                              ref.parse_frame(frame))

    for bad in (b"", b"\xaa", b"\xaa\x08", b"\xaa\x08\x00"):
        check_raises(f"parse_frame({bad!r})", core.parse_frame, bad)
    check_raises("parse_frame bad SOF", core.parse_frame, b"\xbb\x08\x00\x00")


# --------------------------------------------------------------------------
# 5. StreamDecoder, on real recorded traffic
# --------------------------------------------------------------------------
def parity_stream_decoder(ref, core) -> None:
    for fixture in ("mode-cycle.jsonl", "spp-smoketest.jsonl"):
        rows = load_fixture_hexes(fixture)
        if not rows:
            continue
        stream = b"".join(bytes.fromhex(h) for _d, h in rows)
        check(f"{fixture}: stream non-empty", len(stream) > 0, True)

        # whole-stream, then byte-by-byte, then in random chunks -- a decoder
        # that only works on aligned reads is a decoder that fails on RFCOMM
        for label, chunks in (
            ("whole", [stream]),
            ("byte-wise", [stream[i:i + 1] for i in range(len(stream))]),
            ("chunked", _random_chunks(stream, random.Random(7))),
        ):
            ref_dec = ref.StreamDecoder()
            core_dec = core.StreamDecoder()
            ref_events, core_events = [], []
            for chunk in chunks:
                ref_events += ref_dec.feed(chunk)
                core_events += core_dec.feed(chunk)
            check(f"{fixture} StreamDecoder [{label}]", core_events, ref_events)
            check(f"{fixture} StreamDecoder [{label}] non-empty", len(core_events) > 0, True)

    # the events we just decoded should include real TL packets
    ref_dec, core_dec = ref.StreamDecoder(), core.StreamDecoder()
    rows = load_fixture_hexes("mode-cycle.jsonl")
    if rows:
        stream = b"".join(bytes.fromhex(h) for _d, h in rows)
        events = core_dec.feed(stream)
        cmds = [e["packet"]["cmd"] for e in events if e["kind"] == "packet"]
        check("mode-cycle contains 0x8404 acks", 0x8404 in cmds, True)
        check("mode-cycle contains 0x0404 writes", 0x0404 in cmds, True)
        check("mode-cycle contains 0x810C reads", 0x810C in cmds, True)


# --------------------------------------------------------------------------
# 4b. a defect in the reference framing, reproduced deliberately
# --------------------------------------------------------------------------
KNOWN_DEFECT_MTU = 130


def check_known_frame_defect(ref, core) -> None:
    """At MTU 130 the reference cannot round-trip, and the port must not either.

    A multi-frame body is ``S - 3`` bytes.  At ``S == 130`` that is exactly 127,
    which ``varint_encode`` emits in **one** byte while ``_varint_size`` reserved
    **two**.  The frame is written with a one-byte hole, and reading it back
    derives a different layout: the parser sees ``declared == 127``, concludes
    the varint was one byte, reads the hole as the control byte (status 0, so no
    FSN follows) and shifts the payload by two bytes.  One malformed frame
    therefore injects exactly two spurious bytes.

    Only ``S == 130`` is affected.  Every other budget either has ``S <= 129``
    (reserve one, write one) or ``S - 3 >= 128`` (reserve two, write two).

    The Air7 Pro uses MTU 512, where body lengths are >= 128 and the two always
    agree, so this never fires on the target device.  Whether the defect exists
    in the APK's ``OPPOv1Wrapper.m``/``.j`` or was introduced when the reference
    was transcribed is **not yet established** -- so the port reproduces the
    behaviour byte for byte rather than inventing a fix.  Correcting it should
    be a deliberate act that changes this test.
    """
    rng = random.Random(0xD0D0)

    # 1. the defect is in the reference, not the port
    data = bytes(rng.randrange(256) for _ in range(255))
    ref_frames = ref.build_frames(data, KNOWN_DEFECT_MTU)
    check("reference is defective at MTU 130", ref.deframe(ref_frames) == data, False)

    # 2. the port reproduces it exactly -- both the frames and the mis-parse
    core_frames = core.build_frames(data, KNOWN_DEFECT_MTU)
    check("port frames identical at MTU 130", core_frames, ref_frames)
    check("port mis-parse identical at MTU 130",
          core.deframe(core_frames), ref.deframe(ref_frames))

    # 3. the minimal case shows the mechanism plainly: two frames, two bytes
    small = bytes(rng.randrange(256) for _ in range(126))
    out = core.deframe(core.build_frames(small, KNOWN_DEFECT_MTU))
    check("defect is a 2-byte leading shift", out, b"\x00\x00" + small)

    # 4. the exact boundary: 125 fits in one varint byte, 126 does not
    for n, expect_ok in ((125, True), (126, False)):
        payload = bytes(rng.randrange(256) for _ in range(n))
        frames = core.build_frames(payload, KNOWN_DEFECT_MTU)
        check(f"MTU 130 round-trip at {n}B", core.deframe(frames) == payload, expect_ok)

    # 5. and the device we actually target is unaffected at every size
    for n in (0, 1, 125, 126, 127, 128, 200, 255, 256, 512, 513, 2000, 4096):
        payload = bytes(rng.randrange(256) for _ in range(n))
        frames = core.build_frames(payload, 512)
        check(f"MTU 512 round-trip at {n}B", core.deframe(frames) == payload, True)


def _random_chunks(data: bytes, rng: random.Random) -> list[bytes]:
    out, i = [], 0
    while i < len(data):
        n = rng.randrange(1, 20)
        out.append(data[i:i + n])
        i += n
    return out


# --------------------------------------------------------------------------
# 6. decoders
# --------------------------------------------------------------------------
def parity_decoders(ref, core) -> None:
    cases = [
        ("parse_capabilities", "00bf4f5ae665dc4004"),
        ("parse_capabilities", "000000000000000000"),
        ("parse_capabilities", "ff"),
        # ``parse_feature_detect`` is deliberately absent from tlcore: the unit
        # never answers ``0x012A``, so the decoder was removed rather than
        # shipped unverified.  The reference out-of-tree copy still has it, so
        # it is not a parity case.
        ("parse_notification_event", "03010101"),
        ("parse_notification_event", "01036402640300"),
        ("parse_notification_event", "0401040204"),
        ("parse_compactness_event", "01040204"),
        ("parse_compactness_event", "040104"),
        ("parse_compactness_event", ""),
        ("parse_notification_event", "02"),
        ("parse_notification_event", ""),
    ]
    for fn_name, hexstr in cases:
        payload = bytes.fromhex(hexstr)
        label = f"{fn_name}({hexstr or '<empty>'})"
        try:
            want, ref_exc = getattr(ref, fn_name)(payload), None
        except Exception as exc:
            want, ref_exc = None, type(exc).__name__
        try:
            got, core_exc = getattr(core, fn_name)(payload), None
        except Exception as exc:
            got, core_exc = None, type(exc).__name__
        if ref_exc or core_exc:
            # e.g. parse_capabilities guards on a status byte + >=1 bitmap byte
            check(f"{label} raises identically", core_exc, ref_exc)
        else:
            check(label, got, want)

    # the Air7 Pro bitmap is the one live value we must never drift from
    caps = core.parse_capabilities(bytes.fromhex("00bf4f5ae665dc4004"))
    check("Air7 Pro capability command count", len(caps["commands"]), 49)
    check("Air7 Pro capability ok", caps["ok"], True)

    # state decoders (new in the port; no reference counterpart)
    check("parse_battery Air7 Pro",
          core.parse_battery(bytes.fromhex("0003016402640300")),
          {"status": 0, "devices": {"L": {"level": 100, "charging": False},
                                    "R": {"level": 100, "charging": False},
                                    "CASE": {"level": 0, "charging": False}}})
    check("parse_battery charging bit",
          core.parse_battery(bytes.fromhex("000101e1"))["devices"]["L"],
          {"level": 97, "charging": True})
    check("parse_use_time Air7 Pro",
          core.parse_use_time(bytes.fromhex("000301cfe0000002ade100000300000000")),
          {"status": 0, "devices": {"L": 57551, "R": 57773, "CASE": 0}})
    check("parse_earbud_status in_ear",
          core.parse_earbud_status(bytes.fromhex("000303030304"))["in_ear"], True)
    check("parse_model_id Air7 Pro",
          core.parse_model_id(bytes.fromhex("00124c06"))["model_id"], 0x064C12)
    check("parse_device_version Air7 Pro",
          core.parse_device_version(b"\x00" + b"1.1.0.85")["versions"], ["1.1.0.85"])
    check("parse_high_volume on",
          core.parse_high_volume(bytes.fromhex("0001")), {"status": 0, "enabled": True})
    check("parse_high_volume off",
          core.parse_high_volume(bytes.fromhex("0000")), {"status": 0, "enabled": False})
    check("parse_high_volume short",
          core.parse_high_volume(b"\x00"), {"status": 0, "enabled": None})
    check("parse_ai_status active",
          core.parse_ai_status(bytes.fromhex("000101")),
          {"status": 0, "state": 1, "type": 1})
    check("parse_ai_status idle",
          core.parse_ai_status(bytes.fromhex("000000")),
          {"status": 0, "state": 0, "type": 0})
    check("parse_ai_status short",
          core.parse_ai_status(b"\x00"), {"status": 0, "state": None, "type": None})
    check("parse_public_event ai",
          core.parse_public_event(bytes.fromhex("02000101")),
          {"flags": 2, "volume": None, "ai": {"state": 1, "type": 1}})
    check("parse_public_event volume",
          core.parse_public_event(bytes.fromhex("080000")),
          {"flags": 8, "volume": 0, "ai": None})
    check("parse_multi_connect",
          core.parse_multi_connect(bytes.fromhex(
              "00026655443322110c0201094c6170746f702d4100"
              "ffeeddccbbaa0b00000850686f6e652d4200")),
          {"status": 0, "count": 2, "entries": [
              {"mac": "11:22:33:44:55:66", "name": "Laptop-A", "state": 2, "flag": 1},
              {"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone-B", "state": 0, "flag": 0}]})
    check("parse_multi_connect short", core.parse_multi_connect(b"\x00"),
          {"status": 0, "count": 0, "entries": []})
    # the live 0x812C body from a Buds Air7 Pro (2026-10-22); values are signed
    check("parse_multi_bass_engine Air7 Pro",
          core.parse_multi_bass_engine(bytes.fromhex("0003fb050001fb050002fb050003")),
          {"status": 0, "count": 3, "entries": [
              {"min": -5, "max": 5, "current": 0, "type": 1},
              {"min": -5, "max": 5, "current": 0, "type": 2},
              {"min": -5, "max": 5, "current": 0, "type": 3}]})
    check("parse_multi_bass_engine short",
          core.parse_multi_bass_engine(b"\x00\x03"),
          {"status": 0, "count": 3, "entries": []})
    multi_event_body = bytes.fromhex(
        "026655443322110c0201094c6170746f702d4100"
        "ffeeddccbbaa0b00000850686f6e652d4200")
    check("parse_multi_connect_event",
          core.parse_multi_connect_event(multi_event_body),
          {"count": 2, "entries": [
              {"mac": "11:22:33:44:55:66", "name": "Laptop-A", "state": 2, "flag": 1},
              {"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone-B", "state": 0, "flag": 0}]})
    check("parse_multi_connect_event matches the reference",
          core.parse_multi_connect_event(multi_event_body),
          ref.parse_multi_connect_event(multi_event_body))
    check("event 0x06 is wired into parse_notification_event",
          core.parse_notification_event(bytes([0x06]) + multi_event_body)["multi_connect"]["count"],
          2)
    check("parse_personalized_anc none",
          core.parse_personalized_anc(bytes.fromhex("0000")),
          {"status": 0, "exists": 0})
    check("parse_personalized_anc stored",
          core.parse_personalized_anc(bytes.fromhex("0001")),
          {"status": 0, "exists": 1})
    check("parse_personalized_anc short", core.parse_personalized_anc(b"\x00"),
          {"status": 0, "exists": None})
    check("parse_notification_capabilities Air7 Pro",
          core.parse_notification_capabilities(bytes.fromhex("00020201")),
          {"status": 0, "count": 2, "types": [2, 1]})
    check("parse_notification_capabilities short",
          core.parse_notification_capabilities(b"\x00"),
          {"status": 0, "count": 0, "types": []})
    check("parse_remote_vendor_id",
          core.parse_remote_vendor_id(bytes.fromhex("002f07")),
          {"status": 0, "vendor_id": 0x072F})
    check("parse_remote_vendor_id short",
          core.parse_remote_vendor_id(b"\x00")["vendor_id"], None)
    check("parse_protocol_version",
          core.parse_protocol_version(bytes.fromhex("00000001")),
          {"status": 0, "data": "000001"})
    check("parse_upgrade_capability",
          core.parse_upgrade_capability(bytes.fromhex("00010103")),
          {"status": 0, "count": 1,
           "entries": [{"device_type": 1, "support_ota": True, "ota_type": 1}]})
    check("parse_upgrade_capability short", core.parse_upgrade_capability(b"\x00"),
          {"status": 0, "count": 0, "entries": []})
    check("parse_color_id", core.parse_color_id(bytes.fromhex("0006")),
          {"status": 0, "color_id": 6})


# --------------------------------------------------------------------------
# 7. payload builders
# --------------------------------------------------------------------------
def parity_payloads(ref, core) -> None:
    for action in range(0, 6):
        for noise_type in range(0, 4):
            for value in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0xFF):
                label = f"payload_set_noise_reduction({action},{noise_type},0x{value:02X})"
                check(label,
                      core.payload_set_noise_reduction(action, noise_type, value),
                      ref.payload_set_noise_reduction(action, noise_type, value))
    for value in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20):
        check(f"payload_set_noise_mode(0x{value:02X})",
              core.payload_set_noise_mode(value), ref.payload_set_noise_mode(value))
    for value in (0, 1, 2, 255):
        check(f"payload_set_high_volume({value})",
              core.payload_set_high_volume(value), ref.payload_set_high_volume(value))
    for value in (0, 1, 2, 255):
        check(f"payload_set_find_mode({value})",
              core.payload_set_find_mode(value), ref.payload_set_find_mode(value))
    for value in (0, 1, 2, 255):
        check(f"payload_compactness_detect({value})",
              core.payload_compactness_detect(value), ref.payload_compactness_detect(value))
    for state in (0, 1, 2, 255):
        for ai_type in (0, 1, 2, 4, 255):
            check(f"payload_set_ai_status({state},{ai_type})",
                  core.payload_set_ai_status(state, ai_type),
                  ref.payload_set_ai_status(state, ai_type))
    for entries in ([], [b"\x01\x02\x03\x04"], [b"\x01", b"\x02"]):
        check(f"payload_set_key_function({entries!r})",
              core.payload_set_key_function(entries), ref.payload_set_key_function(entries))
    # payload_set_eq (0x0418) is deliberately not shipped -- the APK refuses to
    # send a zero-band record and the band table only the vendor editor has is
    # unobtainable -- so it is not compared.  The reference keeps its copy: that
    # file is the frozen static reconstruction, not the shipped surface.

    # crc16 is unused on the OPPOv1 path but ported, so keep it honest
    for data in (b"", b"\x00", b"123456789", bytes(range(256))):
        check(f"crc16_ccitt({data[:8]!r})", core.crc16_ccitt(data), ref.crc16_ccitt(data))


# --------------------------------------------------------------------------
# 9. the rule the split depends on
# --------------------------------------------------------------------------
# Importing any of these from tlcore would break the boundary described in
# docs/architecture.md 2: the core must be exercisable on a machine with no
# radio, in CI, and eventually from another language.  The rule is easy to
# break by accident (one convenient `import serial` inside a decoder) and
# impossible to notice by reading, so it is checked mechanically.
BANNED_IN_CORE = (
    # platform transports
    "IOBluetooth", "objc", "Foundation", "AppKit",
    "serial", "serial.tools.list_ports", "socket", "bluetooth", "bleak",
    # UI toolkits
    "PySide6", "PyQt5", "PyQt6", "shiboken6",
)


def test_core_purity() -> None:
    """`tlcore` must import nothing platform-specific.

    Run in a clean subprocess rather than in-process, because this harness has
    already imported the reference module and a pile of stdlib.
    """
    probe = (
        "import json, sys"
        f"; import tlcore; print(json.dumps(sorted(set(sys.modules) & set({BANNED_IN_CORE!r}))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    check("tlcore imports cleanly in a bare interpreter", proc.returncode, 0)
    if proc.returncode != 0:
        print(proc.stderr)
        return
    check("tlcore pulls in no platform binding", json.loads(proc.stdout), [])


# --------------------------------------------------------------------------
# 10. the write that must never change
# --------------------------------------------------------------------------
def parity_golden_write(ref, core) -> None:
    """The exact frame the Air7 Pro accepted, byte for byte."""
    packet = core.build_packet(core.CMD_SET_NOISE_REDUCTION,
                               core.payload_set_noise_mode(core.NOISE_MODE_NORMAL), 0)
    check("0x0404 action=1 payload", core.payload_set_noise_mode(0x01), b"\x01\x01\x01")
    check("0x0404 packet", packet.hex(), "0404000300010101")
    frames = core.build_frames(packet, 512)
    check("0x0404 frame count", len(frames), 1)
    check("0x0404 frame", frames[0].hex(), "aa0a00000404000300010101")

    # and the recorded write from the live run
    rows = load_fixture_hexes("mode-cycle.jsonl")
    live_writes = [h for d, h in rows if d == "tx" and h[8:12] == "0404"]
    check("recorded 0x0404 writes exist", len(live_writes) > 0, True)
    if live_writes:
        check("recorded write matches builder", live_writes[0], frames[0].hex())


def main() -> int:
    if REFERENCE_PATH is None:
        print("TL_REFERENCE is not set; skipping the parity check.")
        print("Set TL_REFERENCE=/path/to/tl_frame.py to run it against the reference codec.")
        return 0
    ref = load_reference(REFERENCE_PATH)
    import tlcore
    from tlcore import payloads, wire

    print(f"reference: {REFERENCE_PATH}")
    print(f"tlcore:    {tlcore.__file__}")
    print()

    parity_constants(ref, tlcore)
    parity_packet(ref, wire)
    parity_varint(ref, wire)
    parity_framing(ref, wire)
    check_known_frame_defect(ref, wire)
    parity_stream_decoder(ref, wire)
    parity_decoders(ref, tlcore)
    parity_payloads(ref, payloads)
    test_core_purity()
    parity_golden_write(ref, tlcore)

    print(f"checks run: {CHECKS}")
    if FAILURES:
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES[:40]:
            print(f"  - {failure}")
        if len(FAILURES) > 40:
            print(f"  ... and {len(FAILURES) - 40} more")
        return 1
    print("PARITY: OK -- tlcore reproduces the reference codec byte for byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
