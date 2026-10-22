"""GUI smoke test: build the window offscreen and connect against a fixture.

Skipped cleanly when PySide6 is not installed, so the pure-core test run and CI
without a GUI toolkit are unaffected. It never opens a real window: the Qt
platform is forced to ``offscreen``.

    pytest tests/test_app_gui.py
    QT_QPA_PLATFORM=offscreen python -m pytest -q
"""
from __future__ import annotations

import os
import pathlib
import socket
import sys

import pytest

# Choose the headless Qt platform before the app is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore as t  # noqa: E402
from tlapp import ConnectionOptions  # noqa: E402
from tlapp.window import (  # noqa: E402
    TAB_BACKUP,
    TAB_EVENTS,
    TAB_GESTURES,
    TAB_SOUND,
    MainWindow,
)

#: The Air7 Pro's 0x8108 body: 5 gestures x left/right, long press bound to 8.
GESTURE_BODY = ("000a0201010002010201020103060201040802010600"
                "0101010001010201010103050101040801010600")


def _gesture_table(entries=None) -> dict:
    if entries is None:
        entries = t.parse_key_function_table(bytes.fromhex(GESTURE_BODY))["entries"]
    entries = list(entries)
    return {"status": 0, "count": len(entries), "entries": entries}


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["tlapp"])


def test_window_builds_and_connects() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        assert not window.controller.connected
        window.connect_device()
        app.processEvents()

        assert window.controller.connected
        assert window.lbl_model.text().startswith("realme Buds Air7 Pro")
        assert window.lbl_firmware.text() == "1.1.0.85"
        assert "49 commands advertised" in window.lbl_caps.text()
        assert window.caps_table.rowCount() == len(t.ALL_COMMANDS)
        # the capture advertises setNoiseReduction, so Apply must be enabled
        assert window.noise_apply_btn.isEnabled()
        assert "Normal" in window.lbl_noise.text()

        # the Sound tab carries the equalizer and high-volume controls too
        assert window.eq_combo.count() == 7           # verified presets 0..6
        assert window.eq_combo.currentData() == 0
        assert window.eq_table.columnCount() == 6
        assert window.eq_table.rowCount() == 0        # not read until asked
        assert window.eq_apply_btn.isEnabled()        # capture advertises 0x0406
        assert window.volume_apply_btn.isEnabled()    # capture advertises 0x0407
        assert not window.volume_check.isChecked()

        # the Backup tab is live too, empty until something is captured
        assert window.tabs.isTabEnabled(TAB_BACKUP)
        assert window.backup_table.rowCount() == 0
        assert window.restore_table.rowCount() == 0
        assert window.backup_path_edit.text() == ""

        # the event monitor exists but is idle until asked
        assert window.tabs.isTabEnabled(TAB_EVENTS)
        assert not window.events_timer.isActive()
        assert window.listen_btn.text() == "Start listening"
        assert window.events_table.columnCount() == 3

        # the gesture table is readable and writable on this unit, but empty
        # until read -- and nothing can be applied before that
        assert window.tabs.isTabEnabled(TAB_GESTURES)
        assert window.gestures_read_btn.isEnabled()      # 0x0108 advertised
        assert window.long_press_btn.isEnabled()         # 0x0401 advertised
        assert not window.gestures_apply_btn.isEnabled()
        assert window.gestures_table.rowCount() == 0
        assert window.lbl_gestures.text() == "No table read yet."
    finally:
        window.close()


def test_gestures_tab_stages_edits_and_reverts() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()

        window._show_gestures(_gesture_table())
        assert window.gestures_table.rowCount() == 10
        assert window.gestures_table.columnCount() == 5
        assert window.gestures_table.item(0, 0).text() == "right"
        assert window.gestures_table.item(0, 2).text() == "single-click"
        assert window.gestures_table.item(3, 2).text() == "long-press"
        assert window.gestures_table.item(3, 4).text() == "noise-control"

        combo = window.gestures_table.cellWidget(3, 3)   # right long-press
        assert combo.currentData() == 8
        assert combo.currentText() == "noise-control"

        # nothing edited yet, so there is nothing to apply
        assert not window.gestures_apply_btn.isEnabled()
        assert "no unsaved changes" in window.lbl_gestures.text()

        # stage one edit; the row stays in the table, only the combo moves
        combo.setCurrentIndex(combo.findData(0))
        assert window.gestures_apply_btn.isEnabled()
        assert "1 unsaved change" in window.lbl_gestures.text()
        assert "right long-press" in window.lbl_gestures.text()

        # 'disable long press' only stages: the right row is already staged, so
        # it moves the left one and the pending count goes to two
        window._stage_long_press_off()
        assert "Staged long press off for 1 gesture" in window.statusBar().currentMessage()
        assert "2 unsaved change" in window.lbl_gestures.text()
        assert window.gestures_table.cellWidget(8, 3).currentData() == 0  # left

        # revert puts every combo back to what the device reported
        window._revert_gestures()
        assert not window.gestures_apply_btn.isEnabled()
        assert window.gestures_table.cellWidget(3, 3).currentData() == 8
        assert "no unsaved changes" in window.lbl_gestures.text()
    finally:
        window.close()


def test_gestures_tab_keeps_a_function_it_has_no_name_for() -> None:
    """An unknown function byte must stay selectable, not be rewritten to off."""
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_gestures(_gesture_table([t.KeyFunctionInfo(1, 1, 1, 0x42)]))
        assert window.gestures_table.rowCount() == 1
        combo = window.gestures_table.cellWidget(0, 3)
        assert combo.currentData() == 0x42
        assert "unnamed" in combo.currentText()
        assert window._gesture_updates() == {}
    finally:
        window.close()


def test_gesture_controls_are_capability_gated() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        assert window.gestures_read_btn.isEnabled()

        window.controller.supports = lambda cmd: False
        window._apply_capability_gating()
        assert not window.gestures_read_btn.isEnabled()
        assert not window.long_press_btn.isEnabled()
        assert not window.gestures_apply_btn.isEnabled()

        window.controller.supports = lambda cmd: True
        window._apply_capability_gating()
        assert window.gestures_read_btn.isEnabled()
        assert window.long_press_btn.isEnabled()
        # still nothing staged, so Apply stays off even when the write is supported
        assert not window.gestures_apply_btn.isEnabled()
    finally:
        window.close()


def test_event_monitor_toggles_renders_and_clears() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()

        window._toggle_listening()
        assert window.events_timer.isActive()
        assert window.listen_btn.text() == "Stop listening"
        assert "Listening" in window.lbl_events.text()

        # the capture still holds one unprompted 0x0500 device -> app request
        window._poll_events()
        assert window.events_table.rowCount() == 1
        assert window.events_table.item(0, 1).text() == "requestHostTime"
        assert "request" in window.events_table.item(0, 2).text()

        # newest first, and every shape gets a readable phrase
        window._add_event_row({"cmd": "0x0204", "name": "notificationEvent",
                               "event": "battery", "tlv": {1: 100, 2: 100, 3: 0}})
        window._add_event_row({"cmd": "0x0204", "name": "notificationEvent",
                               "event": "userInteraction",
                               "press": {"function_name": "off", "device_type": 1,
                                         "button": 1, "button_action": 2, "scene": 3}})
        assert window.events_table.rowCount() == 3
        assert window.events_table.item(0, 1).text() == "userInteraction"
        assert "button press: off" in window.events_table.item(0, 2).text()
        assert window.events_table.item(1, 1).text() == "battery"
        assert "1=100" in window.events_table.item(1, 2).text()

        window._toggle_listening()
        assert not window.events_timer.isActive()
        assert window.listen_btn.text() == "Start listening"
        assert "Not listening" in window.lbl_events.text()

        window._clear_events()
        assert window.events_table.rowCount() == 0

        # 0x0200 is ungated, so a synthetic reply is enough to check the report
        window._show_event_types({"count": 2, "types": [2, 1]})
        assert "2 event type(s)" in window.statusBar().currentMessage()
        assert "0x02, 0x01" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_disconnecting_stops_the_event_monitor() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        window._toggle_listening()
        assert window.events_timer.isActive()

        window._toggle_connection()   # disconnect
        app.processEvents()
        assert not window.controller.connected
        assert not window.events_timer.isActive()
        assert window.listen_btn.text() == "Start listening"
        assert not window.tabs.isTabEnabled(TAB_EVENTS)
    finally:
        window.close()


def test_backup_tab_renders_a_summary_and_a_report() -> None:
    """The Backup tab's display handlers, with no device involved."""
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_backup_summary({
            "created": "2026-10-20T12:00:00+00:00",
            "model": "0x064C12", "firmware": ["1.1.0.85"],
            "captured": 18, "skipped": 2,
            "restorable": ["eq_mode", "high_volume"],
            "captured_only": ["multi_point", "status_0107"],
            "contains_paired_devices": True,
        })
        assert "0x064C12" in window.lbl_backup.text()
        assert "1.1.0.85" in window.lbl_backup.text()
        assert "18 response(s) captured" in window.lbl_backup.text()
        assert window.backup_table.rowCount() == 4
        assert window.backup_table.item(0, 1).text() == "restorable"
        assert "paired-device" in window.backup_table.item(2, 1).text()

        window._show_restore_report({
            "applied": [{"setting": "eq_mode", "dry_run": True}],
            "not_restored": [{"setting": "multi_point",
                               "reason": "captured, no restorer"}],
        })
        assert window.restore_table.rowCount() == 2
        assert window.restore_table.item(0, 1).text() == "would apply (dry run)"
        assert window.restore_table.item(1, 1).text() == "captured, no restorer"

        # an empty snapshot must say so rather than render an empty table
        window._show_restore_report({"applied": [], "not_restored": []})
        assert window.restore_table.rowCount() == 1
        assert window.restore_table.item(0, 0).text() == "(nothing)"
    finally:
        window.close()


def test_sound_apply_buttons_are_capability_gated() -> None:
    """A unit that does not advertise a write must have its Apply disabled."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        # connect first: isEnabled() is false for anything under a disabled tab,
        # so the Sound tab must be live before the gating itself can be read
        window.connect_device()
        app.processEvents()
        assert window.eq_apply_btn.isEnabled()

        window.controller.supports = lambda cmd: False
        window._apply_capability_gating()
        assert not window.noise_apply_btn.isEnabled()
        assert not window.eq_apply_btn.isEnabled()
        assert not window.volume_apply_btn.isEnabled()

        window.controller.supports = lambda cmd: True
        window._apply_capability_gating()
        assert window.noise_apply_btn.isEnabled()
        assert window.eq_apply_btn.isEnabled()
        assert window.volume_apply_btn.isEnabled()
    finally:
        window.close()


def test_instance_lock_is_exclusive() -> None:
    import tempfile

    from tlapp.instance import InstanceLock

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "app.lock"
        first = InstanceLock(path)
        try:
            assert first.acquired
            second = InstanceLock(path)
            try:
                assert not second.acquired, "a second instance must not acquire the lock"
                # the refusal must name the holder so the message is actionable
                assert "already running" in second.explain()
                assert str(os.getpid()) in second.explain()
            finally:
                second.release()
        finally:
            first.release()
        # once released, a fresh instance acquires it again
        third = InstanceLock(path)
        try:
            assert third.acquired
        finally:
            third.release()


def test_instance_lock_reclaims_a_dead_holder() -> None:
    """A lock left behind by a killed process must not lock the app out."""
    import tempfile

    from tlapp.instance import InstanceLock

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "app.lock"
        # QLockFile stores pid / appname / hostname; a dead pid on this host
        # is what makes a lock stale and reclaimable
        path.write_text(f"{999_999_999}\ntlapp\n{socket.gethostname()}\n")
        assert path.exists()
        fresh = InstanceLock(path)
        try:
            assert fresh.acquired, "a stale lock must be reclaimed"
        finally:
            fresh.release()


def test_default_lock_path_is_writable() -> None:
    from tlapp.instance import default_lock_path

    path = default_lock_path()
    assert path.parent.is_dir()
    assert path.name == "open-real-link.lock"


def _run_app(args: list[str], env_extra: dict, wait: float = 6.0):
    """Launch ``python -m tlapp`` in a real subprocess.

    Returns ``(returncode, stderr)``. ``returncode`` is ``None`` if the process
    was still running after ``wait`` seconds (i.e. it reached the event loop) --
    in that case it is terminated.
    """
    import subprocess

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.update(env_extra)
    proc = subprocess.Popen([sys.executable, "-m", "tlapp", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, cwd=str(ROOT))
    try:
        _out, err = proc.communicate(timeout=wait)
        return proc.returncode, err
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return None, ""


def test_main_starts_and_reaches_the_event_loop() -> None:
    """A real launch must get past setup (app name, lock, window) and stay up.

    This is what caught a startup crash: the widget tests never run ``main``.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        rc, err = _run_app(["--replay", "spp-smoketest.jsonl"],
                           {"OPEN_REAL_LINK_LOCK": str(pathlib.Path(tmp) / "app.lock")})
    assert rc is None, f"app exited early (rc={rc}):\n{err}"


def test_main_refuses_a_second_instance() -> None:
    """With the lock held, a launch must exit 1 quickly and say why."""
    import tempfile

    from tlapp.instance import InstanceLock

    with tempfile.TemporaryDirectory() as tmp:
        lock_path = pathlib.Path(tmp) / "app.lock"
        holder = InstanceLock(lock_path)
        try:
            assert holder.acquired
            rc, err = _run_app(["--replay", "spp-smoketest.jsonl"],
                               {"OPEN_REAL_LINK_LOCK": str(lock_path)})
        finally:
            holder.release()
    assert rc == 1, f"a second instance must exit 1, got {rc}\n{err}"
    assert "already running" in err, err
    assert "tlapp:" in err, err


def test_window_disconnected_state() -> None:
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        # Sound, Gestures, Backup and Events are disabled until a device is connected
        assert window.connect_btn.text() == "Connect"
        assert not window.tabs.isTabEnabled(TAB_SOUND)
        assert not window.tabs.isTabEnabled(TAB_GESTURES)
        assert not window.tabs.isTabEnabled(TAB_BACKUP)
        assert not window.tabs.isTabEnabled(TAB_EVENTS)
    finally:
        window.close()
