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
    ConnectionOptions,
    ControllerError,
    DeviceController,
    friendly_error,
    noise_choices,
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

