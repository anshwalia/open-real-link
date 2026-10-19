#!/usr/bin/env python3
"""The button / key-function subsystem and the device -> app request family.

Everything here is pure (no I/O).  ``fixtures/key-functions.jsonl`` is a
sanitized golden trace: a recorded ``0x0108`` read and the ``0x0401`` write-back
of the same table, with no device identifiers.  Run directly, or under pytest.

    python3 tests/test_buttons.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore as t  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0

#: The observed Air7 Pro 0x8108 body (10 tuples, no identifiers).
KF_TABLE = bytes.fromhex(
    "000a"
    "02010100" "02010201" "02010306" "02010408" "02010600"
    "01010100" "01010201" "01010305" "01010408" "01010600")


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def check_key_function_info() -> None:
    entry = t.KeyFunctionInfo(2, 1, 3, 6)
    check("to_bytes", entry.to_bytes(), bytes.fromhex("02010306"))
    check("from_bytes", t.KeyFunctionInfo.from_bytes(bytes.fromhex("02010306")), entry)
    check("from_bytes offset",
          t.KeyFunctionInfo.from_bytes(b"\xff\x02\x01\x03\x06", 1), entry)
    check("describe", entry.describe(), "right triple-click -> next-track")
    check("describe left prev",
          t.KeyFunctionInfo(1, 1, 3, 5).describe(), "left triple-click -> previous-track")
    check("describe long ANC",
          t.KeyFunctionInfo(2, 1, 4, 8).describe(), "right long-press -> noise-control")
    # every observed function has a label; the maps stay in sync with the decoder
    for fn in (0, 1, 3, 4, 5, 6, 8, 11, 12, 13, 17, 26, 27, 31):
        check(f"function {fn} labelled", fn in t.FUNCTION_NAMES, True)
    for action in (1, 2, 3, 4, 5, 6):
        check(f"action {action} labelled", action in t.ACTION_NAMES, True)
    for dev in (1, 2, 4):
        check(f"deviceType {dev} labelled", dev in t.DEVICE_TYPE_NAMES, True)


def check_parse_key_function_table() -> None:
    table = t.parse_key_function_table(KF_TABLE)
    check("status", table["status"], 0)
    check("count", table["count"], 10)
    check("entry count", len(table["entries"]), 10)
    check("first", table["entries"][0], t.KeyFunctionInfo(2, 1, 1, 0))
    check("right triple -> next", table["entries"][2], t.KeyFunctionInfo(2, 1, 3, 6))
    check("right long -> ANC", table["entries"][3], t.KeyFunctionInfo(2, 1, 4, 8))
    check("left triple -> prev", table["entries"][7], t.KeyFunctionInfo(1, 1, 3, 5))
    check("records are hex", table["records"][0], "02010100")
    check("records match entries",
          [e.to_bytes().hex() for e in table["entries"]], table["records"])
    # a truncated body stops cleanly rather than inventing entries
    short = t.parse_key_function_table(bytes.fromhex("000a02010100"))
    check("short count kept", short["count"], 10)
    check("short entries parsed", len(short["entries"]), 1)
    check("empty body", t.parse_key_function_table(b"")["count"], 0)


def check_key_function_round_trip() -> None:
    """The read body minus its status byte is exactly the 0x0401 write body."""
    table = t.parse_key_function_table(KF_TABLE)
    write = t.payload_set_key_function([e.to_bytes() for e in table["entries"]])
    check("write payload == read body[1:]", write, KF_TABLE[1:])
    check("write count byte", write[0], 10)
    check("write length", len(write), 41)
    # re-parsing the write payload with a fake status byte round-trips
    check("round-trip",
          t.parse_key_function_table(b"\x00" + write)["entries"], table["entries"])


def check_key_function_patch() -> None:
    """Patching and long-press disable only touch the intended rows."""
    table = t.parse_key_function_table(KF_TABLE)["entries"]
    check("long-press actions", t.LONG_PRESS_ACTIONS, (4, 5))

    patched = t.patch_key_functions(table, {(2, 1, 4): 4})  # right long -> voice
    check("patch keeps length", len(patched), len(table))
    check("patch changes the target", patched[3], t.KeyFunctionInfo(2, 1, 4, 4))
    check("patch keeps the others",
          [e for i, e in enumerate(patched) if i != 3],
          [e for i, e in enumerate(table) if i != 3])
    check("patch is a copy", table[3], t.KeyFunctionInfo(2, 1, 4, 8))
    check("unmatched patch is a no-op",
          t.patch_key_functions(table, {(9, 9, 9): 1}), table)

    off = t.disable_long_press(table)
    check("disable keeps length", len(off), len(table))
    check("disable turns long press off", off[3], t.KeyFunctionInfo(2, 1, 4, 0))
    check("disable keeps non-long-press",
          [e for e in off if e.action not in t.LONG_PRESS_ACTIONS],
          [e for e in table if e.action not in t.LONG_PRESS_ACTIONS])
    check("disable is a copy", table[3], t.KeyFunctionInfo(2, 1, 4, 8))
    mixed = [t.KeyFunctionInfo(1, 1, 5, 8), t.KeyFunctionInfo(1, 1, 3, 5)]
    check("disable action 5", t.disable_long_press(mixed)[0], t.KeyFunctionInfo(1, 1, 5, 0))
    check("disable keeps triple", t.disable_long_press(mixed)[1], mixed[1])


def check_user_interaction_event() -> None:
    body = bytes([2, 1, 2, 1, 0, 0x34, 0x12])
    ev = t.parse_user_interaction(body)
    check("device_type", ev["device_type"], 2)
    check("button", ev["button"], 1)
    check("button_action", ev["button_action"], 2)
    check("function", ev["function"], 1)
    check("function_name", ev["function_name"], "play-pause")
    check("scene", ev["scene"], 0)
    check("options LE", ev["options"], [0x1234])
    check("too short", t.parse_user_interaction(bytes([2, 1, 2, 1])), {})
    check("event type constant", t.EVENT_USER_INTERACTION, 0xF1)
    # composition with the notification decoder
    note = t.parse_notification_event(bytes([0xF1]) + body)
    check("notification event name", note["event_name"], "userInteraction")
    check("body feeds parse_user_interaction",
          t.parse_user_interaction(note["body"])["function_name"], "play-pause")


def check_key_function_fixture() -> None:
    """The sanitized golden trace decodes to the same table it was built from."""
    path = ROOT / "fixtures" / "key-functions.jsonl"
    stream = b"".join(bytes.fromhex(json.loads(line)["hex"])
                      for line in path.read_text().splitlines() if line.strip())
    dec = t.StreamDecoder()
    packets = [ev["packet"] for ev in dec.feed(stream) if ev["kind"] == "packet"]
    check("fixture packet count", len(packets), 4)
    check("fixture cmds", [p["cmd"] for p in packets],
          [0x0108, 0x8108, 0x0401, 0x8401])
    check("fixture 0x8108 body", packets[1]["payload"], KF_TABLE)
    check("fixture write == read body[1:]", packets[2]["payload"], KF_TABLE[1:])
    check("fixture ack status", packets[3]["payload"], b"\x00")
    check("fixture table", t.parse_key_function_table(packets[1]["payload"])["count"], 10)


def check_host_time_request() -> None:
    check("reply builder", t.payload_host_time_reply(0x00080A67),
          bytes.fromhex("00670a0800"))
    check("reply builder unix",
          t.payload_host_time_reply(1_700_000_000)[1:], (1_700_000_000).to_bytes(4, "little"))
    req = t.parse_host_time_request(bytes.fromhex("670a0800"))
    check("request raw", req["raw"], "670a0800")
    check("request value_le", req["value_le"], 0x00080A67)
    check("short request", "value_le" in t.parse_host_time_request(b"\x01"), False)


def check_operation_request() -> None:
    check("action 5", t.parse_operation_request(bytes([5])), {"action": 5, "message": 54})
    check("action 6", t.parse_operation_request(bytes([6])), {"action": 6, "message": 55})
    check("action 9", t.parse_operation_request(bytes([9])), {"action": 9, "message": None})
    check("empty", t.parse_operation_request(b""), {"action": None, "message": None})


def check_feature_switch_status() -> None:
    out = t.parse_feature_switch_status(bytes([2, 1, 1, 2, 0]))
    check("count", out["count"], 2)
    check("entries", out["entries"],
          [{"feature_type": 1, "status": 1}, {"feature_type": 2, "status": 0}])
    check("empty", t.parse_feature_switch_status(b""), {"count": 0, "entries": []})
    check("truncated", len(t.parse_feature_switch_status(bytes([3, 1, 1]))["entries"]), 1)


def check_eq_mode_and_bass() -> None:
    check("eq mode", t.parse_eq_mode_request(bytes([4])), {"eq_mode": 4})
    check("eq mode empty", t.parse_eq_mode_request(b""), {"eq_mode": None})
    check("bass full", t.parse_bass_engine_status(bytes([1, 5, 3, 2])),
          {"min": 1, "max": 5, "current": 3, "type": 2})
    check("bass no type", t.parse_bass_engine_status(bytes([1, 5, 3])),
          {"min": 1, "max": 5, "current": 3, "type": None})
    check("bass short", t.parse_bass_engine_status(bytes([1, 5]))["current"], None)


def check_use_time_list() -> None:
    body = bytes([2, 1]) + (57551).to_bytes(4, "little") + bytes([2]) + (57773).to_bytes(4, "little")
    out = t.parse_use_time_list(body)
    check("count", out["count"], 2)
    check("left", out["entries"][0], {"device_type": 1, "device": "L", "seconds": 57551})
    check("right", out["entries"][1], {"device_type": 2, "device": "R", "seconds": 57773})
    check("empty", t.parse_use_time_list(b"")["count"], 0)


def check_oneshot_audio() -> None:
    body = (1).to_bytes(2, "little") + (1).to_bytes(2, "little") \
        + (7).to_bytes(4, "little") + (3).to_bytes(2, "little") + b"\xaa\xbb\xcc"
    out = t.parse_oneshot_audio(body)
    check("frame_num", out["frame_num"], 1)
    check("channel", out["channel"], 1)
    check("frame seq", out["frames"][0]["seq_num"], 7)
    check("frame data", out["frames"][0]["data"], b"\xaa\xbb\xcc")
    check("channel 2",
          t.parse_oneshot_audio((1).to_bytes(2, "little") + (2).to_bytes(2, "little")
                                + (1).to_bytes(4, "little") + (1).to_bytes(2, "little")
                                + b"\x00")["channel"], 2)
    check("short", t.parse_oneshot_audio(b"\x01")["frame_num"], 0)


def check_request_dispatch() -> None:
    check("known", t.decode_request(0x0504, bytes([4])), {"eq_mode": 4})
    check("unknown", t.decode_request(0x0501, b""), None)
    check("registry size", len(t.REQUEST_DECODERS), 8)
    for cmd in (0x0500, 0x0502, 0x0503, 0x0504, 0x0506, 0x0508, 0x050E, 0x0516):
        check(f"0x{cmd:04X} in REQUEST_DECODERS", cmd in t.REQUEST_DECODERS, True)
        check(f"0x{cmd:04X} has a name", t.command_spec(cmd).name != "", True)


def check_custom_eq() -> None:
    # one entry: selected, min 1, max 2, eqId 4, name "x", 1 band (100 Hz, -3 dB)
    body = bytes([1, 1, 1, 2, 4, 1]) + b"x" + bytes([1]) + (100).to_bytes(2, "little") + b"\xfd"
    out = t.parse_custom_eq(body)
    check("count", out["count"], 1)
    check("entry", out["entries"][0],
          {"is_selected": 1, "min": 1, "max": 2, "eq_id": 4, "name": "x",
           "points": [{"freq": 100, "db": -3}]})
    check("empty", t.parse_custom_eq(b"")["count"], 0)


def check_switch_feature_builder() -> None:
    check("switch on", t.payload_set_switch_feature(3, True), b"\x03\x01")
    check("switch off", t.payload_set_switch_feature(3, False), b"\x03\x00")
    check("switch int", t.payload_set_switch_feature(0x12, 1), b"\x12\x01")


def main() -> int:
    for fn in (check_key_function_info, check_parse_key_function_table,
               check_key_function_round_trip, check_key_function_patch,
               check_user_interaction_event,
               check_key_function_fixture, check_host_time_request,
               check_operation_request, check_feature_switch_status,
               check_eq_mode_and_bass, check_use_time_list, check_oneshot_audio,
               check_request_dispatch, check_custom_eq, check_switch_feature_builder):
        fn()
    if FAILURES:
        print(f"checks run: {CHECKS}")
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"checks run: {CHECKS}")
    print("BUTTONS + REQUESTS: OK")
    return 0


def test_buttons_and_requests() -> None:
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
