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
    default_backup_dir,
    eq_preset_choices,
    friendly_error,
    noise_choices,
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
                "status_010b"]


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
        # bass_engine is absent: the Air7 Pro advertises 0x0124 but answers nothing
        check("summary restorable", summary["restorable"],
              ["eq_mode", "high_volume", "key_function", "noise_mode",
               "switch_features"])
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

