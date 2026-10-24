"""Tests for the desktop app's Qt-free controller.

The controller is the whole device half of the app, so it is exercised here with
no display, no radio and no PySide6: capability view and status against a
recorded live capture, and noise get/set against a synthetic recording that
matches the exact frames the controller sends (read, write, read back).

Run directly for the check list; ``tests/test_app_gui.py`` covers the widgets.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore as t  # noqa: E402 - after the sys.path insert above
from tlapp import (  # noqa: E402
    BackupFileError,
    ConnectionOptions,
    ControllerError,
    DeviceController,
    EQ_PRESET_MAX,
    EQ_PRESET_MIN,
    EVENT_POLL_SECONDS,
    default_backup_dir,
    eq_preset_choices,
    event_phrase,
    friendly_error,
    function_choices,
    noise_choices,
    parse_command,
    parse_payload,
    read_snapshot,
    snapshot_summary,
    suggested_name,
    write_snapshot,
)

CHECKS = 0
FAILURES: list[str] = []

#: name of the pytest aggregator -- main() must skip it or it recurses
AGGREGATOR = "test_app"


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
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: raised {type(exc).__name__}, expected {exc_type.__name__}")
        return
    FAILURES.append(f"{label}: did not raise {exc_type.__name__}")


def wire(cmd: int, payload: bytes = b"") -> bytes:
    return b"".join(t.build_frames(t.build_packet(cmd, payload), 512))


def fixture(directory: str, records) -> str:
    path = pathlib.Path(directory) / "app-fixture.jsonl"
    path.write_text("".join(
        json.dumps({"ts": i, "dir": d, "hex": b.hex()}) + "\n"
        for i, (d, b) in enumerate(records)))
    return str(path)


def _noise_records(before: int, after: int, status: int = 0):
    """The exact frames ``noise_mode`` + ``set_noise_mode`` send."""
    return [
        ("tx", wire(0x010C, bytes([1]))),
        ("rx", wire(0x810C, bytes([0, 1, 1, before]))),
        ("tx", wire(0x0404, bytes([1, 1, after]))),
        ("rx", wire(0x8404, bytes([status]))),
        ("tx", wire(0x010C, bytes([1]))),
        ("rx", wire(0x810C, bytes([0, 1, 1, after]))),
    ]


#: The Air7 Pro's `0x0122` body: status 0, one selected record (eqId 4, no bands).
EQ_INFO_BODY = "0001010102040000"


def _eq_records(before: int, after: int) -> list:
    """The exact frames ``eq_mode`` + ``set_eq_mode`` + ``eq_presets`` send."""
    return [
        ("tx", wire(0x010F)),
        ("rx", wire(0x810F, bytes([0, before]))),
        ("tx", wire(0x0406, bytes([after]))),
        ("rx", wire(0x8406, bytes([0]))),
        ("tx", wire(0x010F)),
        ("rx", wire(0x810F, bytes([0, after]))),
        ("tx", wire(0x0122)),
        ("rx", wire(0x8122, bytes.fromhex(EQ_INFO_BODY))),
    ]


def _high_volume_records(before: int, after: int) -> list:
    """The exact frames ``high_volume`` + ``set_high_volume`` send."""
    return [
        ("tx", wire(0x0110)),
        ("rx", wire(0x8110, bytes([0, before]))),
        ("tx", wire(0x0407, bytes([after]))),
        ("rx", wire(0x8407, bytes([0]))),
        ("tx", wire(0x0110)),
        ("rx", wire(0x8110, bytes([0, after]))),
    ]


# --------------------------------------------------------------------------
# 1 + 2: capability view and status, against a recorded live capture
# --------------------------------------------------------------------------
def test_live_capture_capabilities_and_status() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    check("replay flag", ctrl.replay, True)
    ctrl.connect()
    check("connected", ctrl.connected, True)

    caps = ctrl.capabilities()
    check("caps status", caps["status"], 0)
    check("caps ok", caps["ok"], True)
    check("caps count", len(caps["commands"]), 49)
    check("caps bits", len(caps["bits"]), 32)
    check("supports set noise (0x0404)", ctrl.supports(0x0404), True)
    check("supports set eq mode (0x0406)", ctrl.supports(0x0406), True)
    check("supports set high volume (0x0407)", ctrl.supports(0x0407), True)
    check("supports key write (0x0401)", ctrl.supports(0x0401), True)
    check("feature-detect (0x012A) not advertised", ctrl.supports(0x012A), False)

    status = ctrl.status()
    check("status model", status["model"]["model_id"], 0x064C12)
    check("status firmware", status["firmware"]["versions"], ["1.1.0.85"])
    check("status battery devices", sorted(status["battery"]["devices"]), ["CASE", "L", "R"])
    check("status wear in-ear", status["wear"]["in_ear"], True)
    check("status noise mode", status["noise_mode"], 0x01)
    check("status use time has L", "L" in status["use_time"]["devices"], True)
    ctrl.disconnect()
    check("disconnected", ctrl.connected, False)


# --------------------------------------------------------------------------
# compatibility check (the General view's launch banner)
# --------------------------------------------------------------------------
def test_compatibility_assessment_on_live_capture() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    ctrl.connect()
    ctrl.capabilities()
    ctrl.status()
    verdict = ctrl.compatibility()
    check("compat verdict", verdict["verdict"], "compatible")
    check("compat model", verdict["model_name"], "realme Buds Air7 Pro")
    check("compat tier", verdict["tier"], "A")
    check("compat advertised", verdict["advertised"], 49)
    check("compat missing", verdict["missing"], [])
    check("compat feature count", len(verdict["features"]), 7)
    ctrl.disconnect()
    # the caches are dropped with the link, so a stale verdict cannot linger
    check("compat cleared on disconnect", ctrl.compatibility()["verdict"],
          "unsupported")


def test_assess_compatibility_verdicts() -> None:
    from tlapp.controller import assess_compatibility

    unsupported = assess_compatibility(None, None)
    check("no caps -> unsupported", unsupported["verdict"], "unsupported")

    limited = assess_compatibility(
        {"ok": True, "commands": {0x0105, 0x0106, 0x0109, 0x010C, 0x0404}},
        {"model": {"model_id": 0x064C12}})
    check("partial -> limited", limited["verdict"], "limited")
    check("limited names the gap", "Equalizer" in limited["missing"], True)

    unknown = assess_compatibility(
        {"ok": True, "commands": set(range(0x0100, 0x0500))}, None)
    check("unknown model, all features -> compatible", unknown["verdict"],
          "compatible")
    check("unknown model has no name", unknown["model_name"], None)
    check("unknown model is noted", bool(unknown["notes"]), True)

    tier_c = assess_compatibility(
        {"ok": True, "commands": {0x0105, 0x0106, 0x0109, 0x010C, 0x0404,
                                  0x010F, 0x0406, 0x0110, 0x0407, 0x0400,
                                  0x0108, 0x0401, 0x0112}},
        {"model": {"model_id": 0x061012}})   # Air 3S, tier C
    check("tier C -> compatible with a note", tier_c["verdict"], "compatible")
    check("tier C note", any("tier-C" in note for note in tier_c["notes"]), True)


# --------------------------------------------------------------------------
# 3: noise mode get/set
# --------------------------------------------------------------------------
def test_noise_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _noise_records(0x01, 0x20))
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        check("noise read", ctrl.noise_mode(), 0x01)
        result = ctrl.set_noise_mode(0x20)
        check("noise set requested", result["requested"], 0x20)
        check("noise set actual", result["actual"], 0x20)
        check("noise set ok", result["ok"], True)
        ctrl.disconnect()


def test_noise_write_when_not_worn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # the device answers 0x8404 0e when the buds are out; the write is the
        # first frame because the controller raises before it can read back
        path = fixture(tmp, [
            ("tx", wire(0x0404, bytes([1, 1, 0x20]))),
            ("rx", wire(0x8404, bytes([0x0E]))),
        ])
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        err = None
        try:
            ctrl.set_noise_mode(0x20)
        except ControllerError as exc:
            err = str(exc)
        check("not-worn raises", err is not None, True)
        check("not-worn message", "not being worn" in (err or ""), True)
        ctrl.disconnect()


# --------------------------------------------------------------------------
# 4: equalizer preset get / set, and the 0x0122 preset list
# --------------------------------------------------------------------------
def test_eq_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _eq_records(4, 2))
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        check("eq read", ctrl.eq_mode(), 4)
        result = ctrl.set_eq_mode(2)
        check("eq set requested", result["requested"], 2)
        check("eq set actual", result["actual"], 2)
        check("eq set ok", result["ok"], True)
        info = ctrl.eq_presets()
        check("eq info status", info["status"], 0)
        check("eq info count", info["count"], 1)
        check("eq info entry", info["entries"], [
            {"is_selected": 1, "min": 1, "max": 2, "eq_id": 4,
             "name": "", "points": []}])
        ctrl.disconnect()


def test_eq_clamped_write_reports_a_mismatch() -> None:
    """Writing past 6 is clamped by the device; the read-back says so."""
    with tempfile.TemporaryDirectory() as tmp:
        # we write 8 (0x0406 08); the device answers 0x8406 00 but then reports 6
        path = fixture(tmp, [
            ("tx", wire(0x010F)),
            ("rx", wire(0x810F, bytes([0, 4]))),
            ("tx", wire(0x0406, bytes([8]))),
            ("rx", wire(0x8406, bytes([0]))),
            ("tx", wire(0x010F)),
            ("rx", wire(0x810F, bytes([0, 6]))),
        ])
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        check("unclamped read", ctrl.eq_mode(), 4)
        result = ctrl.set_eq_mode(8)
        check("clamped requested", result["requested"], 8)
        check("clamped actual", result["actual"], 6)
        check("clamped ok is False", result["ok"], False)
        ctrl.disconnect()


def test_eq_preset_choices() -> None:
    choices = eq_preset_choices()
    check("eq preset count", len(choices), 7)
    check("eq preset first", choices[0], (0, "Preset 0"))
    check("eq preset last", choices[-1], (6, "Preset 6"))
    check("eq preset range constants", (EQ_PRESET_MIN, EQ_PRESET_MAX), (0, 6))
    check("eq preset values unique", len({v for v, _ in choices}), 7)


# --------------------------------------------------------------------------
# 5: high volume
# --------------------------------------------------------------------------
def test_high_volume_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _high_volume_records(0, 1))
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        check("high volume read", ctrl.high_volume(), False)
        result = ctrl.set_high_volume(True)
        check("high volume requested", result["requested"], True)
        check("high volume actual", result["actual"], True)
        check("high volume ok", result["ok"], True)
        ctrl.disconnect()


# --------------------------------------------------------------------------
# 5b: bass engine (0x012C) -- read-only
# --------------------------------------------------------------------------
#: The body a live Air7 Pro answered to 0x012C: status 0, three bands, each
#: `min -5 / max +5 / current 0`, with the device's own type selector 1/2/3.
BASS_BODY = "0003fb050001fb050002fb050003"


def _bass_records() -> list:
    return [("tx", wire(0x012C)), ("rx", wire(0x812C, bytes.fromhex(BASS_BODY)))]


def test_bass_engine_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _bass_records())
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        info = ctrl.bass_engine()
        check("bass status", info["status"], 0)
        check("bass count", info["count"], 3)
        # signed: `fb` is -5, not 251
        check("bass first band", info["entries"][0],
              {"min": -5, "max": 5, "current": 0, "type": 1})
        check("bass last band", info["entries"][-1],
              {"min": -5, "max": 5, "current": 0, "type": 3})
        check("the request carried no payload",
              ctrl.transport.writes[-1], wire(0x012C))
        ctrl.disconnect()


def test_bass_engine_is_read_only() -> None:
    """There must be no write control: 0x041B's type->band meaning is unverified.

    The controller may expose a read and nothing else; if a future change adds a
    writer it should fail here and be justified, not slip in.
    """
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    for name in dir(ctrl):
        if "bass" in name.lower():
            check(f"{name} is a read", "set" not in name.lower(), True)
    check("no bass writer", hasattr(ctrl, "set_bass_engine"), False)


def test_bass_engine_disconnected() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    check_raises("bass without connect", ControllerError, ctrl.bass_engine)


# --------------------------------------------------------------------------
# 6: find my buds (0x0400) -- the one write with no read pair
# --------------------------------------------------------------------------
def _find_records() -> list:
    """Ring then stop; both answer a bare ``0x8400 00``."""
    return [
        ("tx", wire(0x0400, bytes([1]))),
        ("rx", wire(0x8400, bytes([0]))),
        ("tx", wire(0x0400, bytes([0]))),
        ("rx", wire(0x8400, bytes([0]))),
    ]


def test_find_buds_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _find_records())
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        ringing = ctrl.find_buds(True)
        check("find on requested", ringing["requested"], True)
        check("find on status", ringing["status"], 0)
        check("find on ok", ringing["ok"], True)
        stopped = ctrl.find_buds(False)
        check("find off requested", stopped["requested"], False)
        check("find off ok", stopped["ok"], True)
        check("the ring frame carried 01", ctrl.transport.writes[0],
              wire(0x0400, bytes([1])))
        check("the stop frame carried 00", ctrl.transport.writes[1],
              wire(0x0400, bytes([0])))
        ctrl.disconnect()


def test_find_buds_a_bad_ack_is_an_error() -> None:
    """There is no read-back, so a non-zero ack must not pass as success.

    The real unit answers ``00`` for both values; this pins what happens if one
    ever does not.  Note the write itself is **not** wear-gated -- the live run
    that verified it deliberately took the buds out first, which is the whole
    point of a find-my-buds command.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, [("tx", wire(0x0400, bytes([1]))),
                             ("rx", wire(0x8400, bytes([0x0E])))])
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        check_raises("bad find ack", ControllerError, ctrl.find_buds, True)
        ctrl.disconnect()


def test_find_buds_disconnected() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    check_raises("find without connect", ControllerError, ctrl.find_buds, True)


# --------------------------------------------------------------------------
# 7: paired devices (0x0112) -- a read whose names are the host's own
# --------------------------------------------------------------------------
#: One record: status 0, count 1, MAC `aa:bb:cc:dd:ee:ff` (wire order is
#: least-significant byte first), state 2, flag 1, name "Phone" + NUL.
PAIRED_BODY = "0001ffeeddccbbaa0902010650686f6e6500"


def test_paired_devices() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, [("tx", wire(0x0112)),
                             ("rx", wire(0x8112, bytes.fromhex(PAIRED_BODY)))])
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()
        info = ctrl.paired_devices()
        check("paired count", info["count"], 1)
        check("paired entry", info["entries"][0],
              {"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone",
               "state": 2, "flag": 1})
        check("paired request carried no payload", ctrl.transport.writes[0],
              wire(0x0112))
        ctrl.disconnect()


def test_paired_devices_is_read_only() -> None:
    """Nothing may write this list; it is the host's own identity."""
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    for name in dir(ctrl):
        if "paired" in name.lower() or "multi_connect" in name.lower():
            check(f"{name} is a read", "set" not in name.lower(), True)


def test_paired_devices_disconnected() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    check_raises("paired without connect", ControllerError, ctrl.paired_devices)


# --------------------------------------------------------------------------
# 8: the expert diagnostics
# --------------------------------------------------------------------------
#: The replies the six Expert-tab reads need, using the values a live Air7 Pro
#: reported: vendor 0x072F, protocol 0.0.0.1, colour 6, no switch features,
#: push types {2, 1}, no personalized-ANC result, AI idle.
DIAG_CANNED = [
    (0x0102, "002f07"), (0x0104, "00000001"), (0x010B, "0006"),
    (0x0107, "00010103"), (0x010D, "0000"), (0x0200, "00020201"),
    (0x011A, "0000"), (0x0131, "000000"),
]


def _diag_records() -> list:
    records = []
    for cmd, body in DIAG_CANNED:
        records.append(("tx", wire(cmd)))
        records.append(("rx", wire(cmd | 0x8000, bytes.fromhex(body))))
    return records


def test_expert_diagnostics_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = fixture(tmp, _diag_records())
        ctrl = DeviceController(ConnectionOptions(replay=path))
        ctrl.connect()

        identity = ctrl.identity()
        check("identity vendor", identity["vendor_id"]["vendor_id"], 0x072F)
        check("identity colour", identity["color_id"]["color_id"], 6)
        check("identity protocol", identity["protocol_version"]["data"], "000001")

        check("upgrade capability entries",
              len(ctrl.upgrade_capability()["entries"]), 1)
        # the finding on this unit: the capability is advertised, the list empty
        check("switch features empty", ctrl.switch_features()["entries"], [])
        check("notification types",
              ctrl.notification_capabilities()["types"], [2, 1])
        check("personalized anc", ctrl.personalized_anc()["exists"], 0)
        check("ai status", ctrl.ai_status()["state"], 0)
        ctrl.disconnect()


def test_expert_diagnostics_disconnected() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    for name in ("identity", "upgrade_capability", "switch_features",
                 "notification_capabilities", "personalized_anc", "ai_status"):
        check_raises(f"{name} without connect", ControllerError,
                     getattr(ctrl, name))


def test_expert_diagnostics_are_read_only() -> None:
    """Nothing on the Expert view may write: it is reads and the raw console."""
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    for name in ("identity", "upgrade_capability", "switch_features",
                 "notification_capabilities", "personalized_anc", "ai_status"):
        check(f"{name} is a read", "set" not in name.lower(), True)


# --------------------------------------------------------------------------
# 6: backup / restore -- plain JSON snapshot files, never a database
# --------------------------------------------------------------------------
#: The Air7 Pro's bitmap.  Entries the plan advertises but that have no canned
#: reply here are recorded under ``skipped``, which is the point: a backup must
#: survive a device that answers only some of the poll.
BACKUP_BITMAP = "00bf4f5ae665dc4004"
BACKUP_CANNED = {
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
}

#: The settings the canned replies above derive, in the order ``tlcore.restore``
#: writes them.
RESTORED = ["noise_mode", "key_function", "eq_mode", "high_volume"]

#: Read faithfully but with no recovered writer -- reported, never guessed at.
NOT_RESTORED = ["multi_point", "noise_cycle", "status_0104", "status_0107",
                "status_010b", "switch_features"]


def backup_records() -> list:
    """A recording a ``tlcore.capture`` poll can walk (uncanned entries skip)."""
    records = [("tx", wire(0x0100)),
               ("rx", wire(0x8100, bytes.fromhex(BACKUP_BITMAP)))]
    for cmd, payload, _key in t.READ_PLAN:
        body = BACKUP_CANNED.get((cmd, payload))
        if body is None:
            continue
        records.append(("tx", wire(cmd, payload)))
        records.append(("rx", wire(cmd | 0x8000, bytes.fromhex(body))))
    return records


def restore_records() -> list:
    """A recording a ``tlcore.restore`` of :func:`backup_records` can walk."""
    records = [("tx", wire(0x0100)),
               ("rx", wire(0x8100, bytes.fromhex(BACKUP_BITMAP)))]
    for cmd, payload in (
        (0x0404, bytes.fromhex("010101")),               # noise_mode -> 1
        (0x0401, bytes.fromhex("020201010002010201")),   # key_function
        (0x0406, bytes.fromhex("04")),                   # eq_mode -> 4
        (0x0407, bytes.fromhex("01")),                   # high_volume -> on
    ):
        records.append(("tx", wire(cmd, payload)))
        records.append(("rx", wire(cmd | 0x8000, bytes([0]))))
    return records


def _connected(directory: str, records) -> DeviceController:
    ctrl = DeviceController(ConnectionOptions(replay=fixture(directory, records)))
    ctrl.connect()
    return ctrl


def test_backup_file_names_and_directory() -> None:
    snapshot = {"format": t.BACKUP_FORMAT, "created": "2026-10-20T12:00:00+00:00",
                "device": {"model": "0x064C12"}}
    check("name from the known-device table", suggested_name(snapshot),
          "realme-buds-air7-pro-20261020T120000Z.json")
    check("name falls back to the raw model", suggested_name({**snapshot, "device":
          {"model": "0x999999"}}), "0x999999-20261020T120000Z.json")
    check("name falls back to 'device'", suggested_name(
        {**snapshot, "device": {}, "created": ""}), "device.json")
    check("default directory is a 'backups' directory",
          default_backup_dir().name, "backups")
    check("default directory is per-user",
          pathlib.Path.home() in default_backup_dir().parents, True)


def test_backup_file_refuses_a_foreign_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        foreign = pathlib.Path(tmp) / "foreign.json"
        foreign.write_text('{"hello": "world"}')
        check_raises("a JSON file that is not a snapshot", BackupFileError,
                     read_snapshot, foreign)
        notjson = pathlib.Path(tmp) / "notes.txt"
        notjson.write_text("this is not json")
        check_raises("a file that is not JSON at all", BackupFileError,
                     read_snapshot, notjson)
        check_raises("a file that is not there", BackupFileError,
                     read_snapshot, pathlib.Path(tmp) / "nope.json")
        check_raises("writing a non-snapshot", BackupFileError, write_snapshot,
                     pathlib.Path(tmp) / "x.json", {"format": "something/else"})


def test_backup_writes_an_atomic_plain_json_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, backup_records())
        target = pathlib.Path(tmp) / "nested" / "backup.json"
        path, summary = ctrl.backup_to(target)
        check("the directory is created", path.parent.is_dir(), True)
        check("the file is written", path.is_file(), True)
        check("no temporary file is left behind",
              sorted(p.name for p in path.parent.iterdir()), ["backup.json"])

        # it is plain JSON -- readable with nothing but the stdlib
        reloaded = json.loads(path.read_text())
        check("the file is a snapshot", reloaded["format"], t.BACKUP_FORMAT)
        check("reloading it validates", read_snapshot(path)["format"], t.BACKUP_FORMAT)

        check("summary model", summary["model"], "0x064C12")
        check("summary firmware", summary["firmware"], ["1.1.0.85"])
        check("summary captured", summary["captured"], len(BACKUP_CANNED))
        # switch_features and bass_engine are captured-only now: 0x0403 is a
        # provable no-op and 0x0124 is never answered, so neither has a restorer
        check("summary restorable", summary["restorable"],
              ["eq_mode", "high_volume", "key_function", "noise_mode"])
        check("summary captured_only", summary["captured_only"], NOT_RESTORED)
        check("summary sees the paired-device list",
              summary["contains_paired_devices"], True)
        check("summary counts what the device refused", summary["skipped"] > 0, True)
        ctrl.disconnect()


def test_backup_refreshes_the_capability_view() -> None:
    """A capture negotiates the bitmap itself, so gating must follow it."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, backup_records())
        check("no bitmap before the capture", ctrl.supports(0x0406), False)
        ctrl.backup()
        check("bitmap known after the capture", ctrl.supports(0x0406), True)
        ctrl.disconnect()


def test_preview_restore_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cap_dir = pathlib.Path(tmp) / "cap"
        cap_dir.mkdir()
        ctrl = _connected(str(cap_dir), backup_records())
        path, _summary = ctrl.backup_to(pathlib.Path(tmp) / "backup.json")
        ctrl.disconnect()

        res_dir = pathlib.Path(tmp) / "res"
        res_dir.mkdir()
        ctrl = _connected(str(res_dir), restore_records())
        report = ctrl.preview_restore(path)
        check("preview lists the restorable settings",
              [item["setting"] for item in report["applied"]], RESTORED)
        check("every preview entry is a dry run",
              all(item.get("dry_run") for item in report["applied"]), True)
        check("preview reports the rest",
              sorted(item["setting"] for item in report["not_restored"]), NOT_RESTORED)
        # the recording still holds the write exchanges: nothing was consumed
        check("preview stopped after the capability read", ctrl.transport.idx, 2)
        ctrl.disconnect()


def test_restore_from_a_file_applies_the_settings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cap_dir = pathlib.Path(tmp) / "cap"
        cap_dir.mkdir()
        ctrl = _connected(str(cap_dir), backup_records())
        path, _summary = ctrl.backup_to(pathlib.Path(tmp) / "backup.json")
        ctrl.disconnect()

        res_dir = pathlib.Path(tmp) / "res"
        res_dir.mkdir()
        fixture_path = fixture(str(res_dir), restore_records())
        ctrl = _connected(str(res_dir), restore_records())
        check("the fixture is the one under test", ctrl.options.replay, fixture_path)
        report = ctrl.restore_from(path)
        check("restore lists what it applied",
              [item["setting"] for item in report["applied"]], RESTORED)
        check("a real restore is not a dry run",
              any(item.get("dry_run") for item in report["applied"]), False)
        check("restore reports the rest",
              sorted(item["setting"] for item in report["not_restored"]), NOT_RESTORED)
        ctrl.disconnect()


def test_a_bad_backup_path_is_a_friendly_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, backup_records())
        blocker = pathlib.Path(tmp) / "blocker"
        blocker.write_text("not a directory")
        check_raises("a path under a file", ControllerError, ctrl.backup_to,
                     blocker / "backup.json")
        foreign = pathlib.Path(tmp) / "foreign.json"
        foreign.write_text("{}")
        check_raises("restoring a foreign file", ControllerError,
                     ctrl.restore_from, foreign)
        check_raises("previewing a foreign file", ControllerError,
                     ctrl.preview_restore, foreign)
        ctrl.disconnect()


def test_snapshot_summary_of_a_minimal_snapshot() -> None:
    summary = snapshot_summary({
        "format": t.BACKUP_FORMAT, "created": "2026-01-01T00:00:00+00:00",
        "device": {}, "settings": {"eq_mode": 4}, "raw": {"eq_mode": {}, "model": {}},
        "skipped": [{"key": "ai_status"}]})
    check("minimal summary restorable", summary["restorable"], ["eq_mode"])
    check("minimal summary captured_only", summary["captured_only"], [])
    check("minimal summary skipped", summary["skipped"], 1)
    check("minimal summary has no paired devices",
          summary["contains_paired_devices"], False)


# --------------------------------------------------------------------------
# 7: gesture / key-function table
# --------------------------------------------------------------------------
#: The Air7 Pro's 0x8108 body: 10 rows, 5 gestures x left/right.  Long press is
#: bound to function 8 (noise-control) on both buds.
GESTURE_BODY = ("000a0201010002010201020103060201040802010600"
                "0101010001010201010103050101040801010600")


def gesture_payload(entries) -> bytes:
    """``0x0401``'s payload: ``[count] + records``."""
    return t.payload_set_key_function([entry.to_bytes() for entry in entries])


def gesture_body(entries) -> bytes:
    """``0x8108``'s body: the same bytes with a status byte in front."""
    return b"\x00" + gesture_payload(entries)


def gesture_entries(body: str = GESTURE_BODY) -> list:
    return t.parse_key_function_table(bytes.fromhex(body))["entries"]


def gesture_records(updates=None, read_back_body: str | None = None) -> list:
    """A full read -> whole-table write -> read-back conversation."""
    patched = t.patch_key_functions(gesture_entries(), updates or {})
    return [
        ("tx", wire(0x0108)),
        ("rx", wire(0x8108, bytes.fromhex(GESTURE_BODY))),
        ("tx", wire(0x0401, gesture_payload(patched))),
        ("rx", wire(0x8401, bytes([0]))),
        ("tx", wire(0x0108)),
        ("rx", wire(0x8108, bytes.fromhex(read_back_body or gesture_body(patched).hex()))),
    ]


def read_only_records(body: str = GESTURE_BODY) -> list:
    return [("tx", wire(0x0108)), ("rx", wire(0x8108, bytes.fromhex(body)))]


def test_read_the_gesture_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, read_only_records())
        table = ctrl.gestures()
        check("row count", table["count"], 10)
        check("status", table["status"], 0)
        check("first row", table["entries"][0], t.KeyFunctionInfo(2, 1, 1, 0))
        check("long press is bound",
              [(e.device_type, e.function) for e in table["entries"] if e.action == 4],
              [(2, 8), (1, 8)])
        check("labels resolve", table["entries"][3].describe(),
              "right long-press -> noise-control")
        ctrl.disconnect()


def test_set_gestures_writes_the_whole_table_back() -> None:
    """The replay is strict, so this fails unless all 10 rows are written."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, gesture_records({(1, 1, 4): 0}))
        result = ctrl.set_gestures({(1, 1, 4): 0})
        check("exactly one row changed", result["changed"],
              [{"device_type": 1, "button": 1, "action": 4, "from": 8, "to": 0}])
        check("write status", result["status"], 0)
        check("read back and matched", result["verified"], True)
        check("every row came back", len(result["table"]["entries"]), 10)
        check("the untouched right long press is intact",
              [e.function for e in result["table"]["entries"]
               if (e.device_type, e.action) == (2, 4)], [8])
        ctrl.disconnect()


def test_disable_long_press_changes_only_the_long_press_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, gesture_records({(1, 1, 4): 0, (2, 1, 4): 0}))
        result = ctrl.disable_long_press()
        check("both long presses changed",
              sorted(c["action"] for c in result["changed"]), [4, 4])
        check("and nothing else did",
              {c["device_type"] for c in result["changed"]}, {1, 2})
        check("verified", result["verified"], True)
        check("the table still has 10 rows", len(result["table"]["entries"]), 10)
        ctrl.disconnect()


def test_set_gestures_refuses_a_gesture_the_device_does_not_have() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, read_only_records())
        check_raises("unknown gesture", ControllerError, ctrl.set_gestures, {(9, 9, 9): 1})
        # the refusal must happen before anything is sent
        check("only the read was sent", len(ctrl.transport.writes), 1)
        ctrl.disconnect()


def test_set_gestures_refuses_an_empty_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, read_only_records("0000"))
        check_raises("empty table", ControllerError, ctrl.set_gestures, {(1, 1, 4): 0})
        check_raises("empty table, disable", ControllerError, ctrl.disable_long_press)
        check("nothing was written", len(ctrl.transport.writes), 1)
        ctrl.disconnect()


def test_set_gestures_with_nothing_to_change_does_not_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, read_only_records())
        result = ctrl.set_gestures({(1, 1, 4): 8})   # already 8
        check("no change", result["changed"], [])
        check("no read-back either", result["verified"], None)
        check("only the read was sent", len(ctrl.transport.writes), 1)
        ctrl.disconnect()


def test_a_read_back_that_does_not_match_is_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # the device answers 0x8401 00 but still reports the old table
        ctrl = _connected(tmp, gesture_records({(1, 1, 4): 0},
                                               read_back_body=GESTURE_BODY))
        result = ctrl.set_gestures({(1, 1, 4): 0})
        check("the change was sent", len(result["changed"]), 1)
        check("but it did not stick", result["verified"], False)
        ctrl.disconnect()


def test_function_choices() -> None:
    choices = function_choices()
    check("off is the first choice", choices[0], (0, "off"))
    check("it is the core's map, in order", choices, sorted(t.FUNCTION_NAMES.items()))
    check("every value is distinct", len({v for v, _ in choices}), len(choices))
    check("off really is 0", t.FUNCTION_NAMES[0], "off")
    check("long press is actions 4 and 5", tuple(t.LONG_PRESS_ACTIONS), (4, 5))


# --------------------------------------------------------------------------
# 8: guarded raw console
# --------------------------------------------------------------------------
def test_parse_command() -> None:
    check("0x hex", parse_command("0x0404"), 0x0404)
    check("bare digits are decimal, like tlcli", parse_command("404"), 404)
    check("whitespace is tolerated", parse_command("  1028 "), 1028)
    check("the top of the range", parse_command("0xFFFF"), 0xFFFF)
    check_raises("empty", ControllerError, parse_command, "")
    check_raises("not a number", ControllerError, parse_command, "zz")
    check_raises("negative", ControllerError, parse_command, "-1")
    check_raises("out of range", ControllerError, parse_command, "0x10000")


def test_parse_payload() -> None:
    check("spaces", parse_payload("01 01 20"), b"\x01\x01\x20")
    check("0x prefixes", parse_payload("0x01 0x20"), b"\x01\x20")
    check("commas", parse_payload("01,02"), b"\x01\x02")
    check("no separator at all", parse_payload("0101"), b"\x01\x01")
    check("empty is an empty payload", parse_payload(""), b"")
    check("whitespace only is empty too", parse_payload("   "), b"")
    check_raises("odd digit count", ControllerError, parse_payload, "012")
    check_raises("not hex", ControllerError, parse_payload, "zz")


def test_send_raw_refuses_every_guarded_family() -> None:
    """The app refuses the tone / OTA / debug families, with no override."""
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, [("tx", wire(0x0106)),
                                ("rx", wire(0x8106, bytes.fromhex("0003016402640300")))])
        families = set()
        for cmd in t.DANGEROUS_COMMANDS:
            try:
                ctrl.send_raw(cmd, b"\x01")
                FAILURES.append(f"0x{cmd:04X} was sent but should have been refused")
            except ControllerError as exc:
                families.add(t.dangerous_family(cmd))
                if t.dangerous_family(cmd) not in str(exc):
                    FAILURES.append(f"0x{cmd:04X} refusal does not name the family")
        check("all three families are covered", len(families), 3)
        check("nothing guarded was written", ctrl.transport.writes, [])
        check("every guarded id is in the table", len(t.DANGEROUS_COMMANDS), 16)
        ctrl.disconnect()


def test_send_raw_sends_and_decodes_the_reply() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, [("tx", wire(0x0106)),
                                ("rx", wire(0x8106, bytes.fromhex("0003016402640300")))])
        result = ctrl.send_raw(0x0106, listen=0.05)
        check("the command is echoed", result["cmd"], 0x0106)
        check("one frame was sent", len(result["frames"]), 1)
        check("the frame is the wire form", result["frames"][0], wire(0x0106))
        check("one packet came back", len(result["events"]), 1)
        check("and it was decoded", result["events"][0]["state"]["devices"],
              {"L": {"level": 100, "charging": False},
               "R": {"level": 100, "charging": False},
               "CASE": {"level": 0, "charging": False}})
        ctrl.disconnect()


def test_send_raw_carries_a_payload_and_a_type_byte() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, [("tx", wire(0x010C, b"\x01")),
                                ("rx", wire(0x810C, bytes.fromhex("00010101")))])
        result = ctrl.send_raw(0x010C, b"\x01", listen=0.05)
        check("the payload is on the wire", result["payload"], b"\x01")
        check("the reply came back", len(result["events"]), 1)
        check("an undecoded reply keeps its raw payload",
              result["events"][0]["payload"], "00010101")
        ctrl.disconnect()


def test_send_raw_can_skip_listening() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ctrl = _connected(tmp, [("tx", wire(0x0106))])
        result = ctrl.send_raw(0x0106, listen=0)
        check("nothing was heard", result["events"], [])
        check("but the frame still went out", len(result["frames"]), 1)
        ctrl.disconnect()


# --------------------------------------------------------------------------
# 9: push-event monitor
# --------------------------------------------------------------------------
def test_poll_events_decodes_a_recorded_capture() -> None:
    """fixtures/user-interaction.jsonl is nine unprompted 0x0204 pushes."""
    ctrl = DeviceController(ConnectionOptions(replay="user-interaction.jsonl"))
    ctrl.connect()
    rows = ctrl.poll_events(0.2)
    check("decoded every push", len(rows), 9)
    check("a button press is decoded", rows[0]["press"]["function_name"], "off")
    check("the press keeps its raw fields",
          (rows[0]["press"]["device_type"], rows[0]["press"]["button"],
           rows[0]["press"]["button_action"], rows[0]["press"]["scene"]),
          (1, 1, 2, 3))
    check("a battery push decodes to its TLV", rows[1]["tlv"], {1: 100, 2: 100, 3: 0})
    check("a noise push keeps its event name", rows[5]["event"], "noiseReduction")
    check("the recording is drained", ctrl.poll_events(0.05), [])
    ctrl.disconnect()


def test_poll_events_drains_what_a_request_parked() -> None:
    """A push seen while waiting for a reply must not be lost or shown twice."""
    ctrl = DeviceController(ConnectionOptions(replay="user-interaction.jsonl"))
    ctrl.connect()
    ctrl.session.events.append({"cmd": 0x0204, "payload": bytes.fromhex("0f080003")})
    rows = ctrl.poll_events(0.2)
    check("the parked push comes first", rows[0]["event"], "publicEvent")
    check("the parked push is decoded", rows[0]["public_event"]["volume"], 3)
    check("the park list is drained", ctrl.session.events, [])
    check("nothing else was dropped", len(rows), 10)
    ctrl.disconnect()


def test_poll_events_never_writes_to_the_device() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="user-interaction.jsonl"))
    ctrl.connect()
    ctrl.poll_events(0.1)
    check("listening sent nothing", ctrl.transport.writes, [])
    ctrl.disconnect()


def test_event_types_from_a_recorded_capture() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="notification-caps.jsonl"))
    ctrl.connect()
    ctrl.capabilities()
    caps = ctrl.event_types()
    check("event-type count", caps["count"], 2)
    check("event types", caps["types"], [2, 1])
    ctrl.disconnect()


def test_event_phrase_covers_every_shape() -> None:
    check("button press", event_phrase({
        "cmd": "0x0204", "press": {"function_name": "off", "device_type": 1,
                                      "button": 1, "button_action": 2, "scene": 3}}),
          "button press: off (dev=1 btn=1 action=2 scene=3)")
    check("compactness", event_phrase({
        "cmd": "0x0204", "compactness": [{"side_name": "left", "value": 1},
                                          {"side_name": "right", "value": 0}]}),
          "compactness: left=1, right=0")
    check("multi-connect", event_phrase({
        "cmd": "0x0204", "multi_connect": {"count": 1, "entries": [
            {"mac": "66:55:44:33:22:11", "name": "Laptop-A"}]}}),
          "multiConnect: 1 device(s) Laptop-A")
    check("public event", event_phrase({
        "cmd": "0x0204", "public_event": {"flags": 8, "volume": 3, "ai": None}}),
          "publicEvent flags=0x0008 volume=3")
    check("device -> app request", event_phrase({
        "cmd": "0x0500", "request": {"raw": "e2000000", "value_le": 226}}),
          "request: {'raw': 'e2000000', 'value_le': 226}")
    check("plain TLV", event_phrase({
        "cmd": "0x0204", "event": "battery", "tlv": {1: 100, 2: 100, 3: 0}}),
          "battery {1=100, 2=100, 3=0}")
    check("an event with only a body", event_phrase({
        "cmd": "0x0204", "event": "earScanResult", "body": "0102"}),
          "body 0102")
    check("a state push", event_phrase({"cmd": "0x8109", "state": {"wear": 3}}),
          "{'wear': 3}")
    check("something no decoder understood",
          event_phrase({"cmd": "0x9999", "payload": "deadbeef"}), "deadbeef")
    check("the poll budget is short", 0 < EVENT_POLL_SECONDS <= 0.1, True)


# --------------------------------------------------------------------------
# helpers and error paths
# --------------------------------------------------------------------------
def test_noise_choices() -> None:
    choices = noise_choices()
    check("noise choice count", len(choices), 6)
    check("noise choice first", choices[0], (0x01, "Normal (noise off)"))
    check("noise choice last", choices[-1], (0x20, "ANC smart"))
    check("noise choice values unique", len({v for v, _ in choices}), 6)


def test_friendly_error() -> None:
    check("not-worn friendly", "worn" in friendly_error(t.NotWorn("x")), True)
    check("timeout friendly", "did not answer" in friendly_error(t.Timeout("y")), True)
    check("not-connected friendly",
          "disconnected" in friendly_error(t.NotConnected("z")), True)
    check("unknown falls through", friendly_error(ValueError("boom")), "boom")


def test_requires_connection() -> None:
    ctrl = DeviceController()
    check_raises("status without connect", ControllerError, ctrl.status)
    check_raises("noise without connect", ControllerError, ctrl.noise_mode)
    check_raises("caps without connect", ControllerError, ctrl.capabilities)
    check_raises("set noise without connect",
                 ControllerError, ctrl.set_noise_mode, 0x01)
    check_raises("eq mode without connect", ControllerError, ctrl.eq_mode)
    check_raises("eq presets without connect", ControllerError, ctrl.eq_presets)
    check_raises("set eq without connect", ControllerError, ctrl.set_eq_mode, 4)
    check_raises("high volume without connect", ControllerError, ctrl.high_volume)
    check_raises("set high volume without connect",
                 ControllerError, ctrl.set_high_volume, True)
    with tempfile.TemporaryDirectory() as tmp:
        # a *valid* file, so the refusal is about the connection and not the path
        path = pathlib.Path(tmp) / "backup.json"
        write_snapshot(path, {"format": t.BACKUP_FORMAT, "created": "2026-01-01T00:00:00+00:00",
                              "device": {}, "settings": {}, "raw": {}, "skipped": []})
        check_raises("backup without connect", ControllerError, ctrl.backup)
        check_raises("backup_to without connect", ControllerError, ctrl.backup_to, path)
        check_raises("preview without connect", ControllerError, ctrl.preview_restore, path)
        check_raises("restore without connect", ControllerError, ctrl.restore_from, path)
    check_raises("poll events without connect", ControllerError, ctrl.poll_events)
    check_raises("event types without connect", ControllerError, ctrl.event_types)
    check_raises("gestures without connect", ControllerError, ctrl.gestures)
    check_raises("set gestures without connect", ControllerError, ctrl.set_gestures, {})
    check_raises("disable long press without connect",
                 ControllerError, ctrl.disable_long_press)
    check_raises("send raw without connect", ControllerError, ctrl.send_raw, 0x0106)
    # the guard must fire before the connection check, so it holds even offline
    check_raises("send raw is guarded even when disconnected",
                 ControllerError, ctrl.send_raw, 0x0415)


def test_double_connect_is_safe() -> None:
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    ctrl.connect()
    ctrl.connect()  # no-op, must not raise or drop the session
    check("still connected after double connect", ctrl.connected, True)
    ctrl.disconnect()
    ctrl.disconnect()  # idempotent
    check("still disconnected", ctrl.connected, False)


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
    print("APP: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_app() -> None:
    """pytest entry point.

    The per-test functions above only *record* failures in ``FAILURES``, so
    pytest would pass this file no matter how many checks failed.  This is
    the only test that turns them into a result.
    """
    assert main() == 0

