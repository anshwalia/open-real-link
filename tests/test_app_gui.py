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
    TAB_DEVICE,
    TAB_EVENTS,
    TAB_GESTURES,
    TAB_SOUND,
    VIEW_EXPERT,
    VIEW_GENERAL,
    MainWindow,
)


class _Modal:
    """Answer the window's modal dialogs so a test never blocks on a click.

    Rebinds ``tlapp.window.QMessageBox`` to a stub for the duration, recording
    what was asked and what was warned about.
    """

    def __init__(self, answer: bool = True):
        self.answer = answer
        self.questions: list = []
        self.warnings: list = []

    def __enter__(self):
        import tlapp.window as window_module

        self._module = window_module
        self._original = window_module.QMessageBox
        outer = self
        real = window_module.QMessageBox.StandardButton

        class Stub:
            StandardButton = real

            @staticmethod
            def question(_parent, title, text, *args, **kwargs):
                outer.questions.append((title, text))
                return Stub.StandardButton.Yes if outer.answer \
                    else Stub.StandardButton.No

            @staticmethod
            def warning(_parent, title, text, *args, **kwargs):
                outer.warnings.append((title, text))
                return Stub.StandardButton.Ok

        window_module.QMessageBox = Stub
        return self

    def __exit__(self, *exc):
        self._module.QMessageBox = self._original
        return False

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

        # the launch compatibility check ran from the same reads
        assert window._compat is not None
        assert window._compat["verdict"] == "compatible"
        assert "realme Buds Air7 Pro" in window._compat["headline"]
        assert "every general feature" in window.compat_label.text()
        assert window.compat_details_btn.isEnabled()

        # the Sound tab carries the equalizer and high-volume controls too
        assert window.eq_combo.count() == 7           # verified presets 0..6
        assert window.eq_combo.currentData() == 0
        assert window.eq_table.columnCount() == 6
        assert window.eq_table.rowCount() == 0        # not read until asked
        assert window.eq_apply_btn.isEnabled()        # capture advertises 0x0406
        assert window.volume_apply_btn.isEnabled()    # capture advertises 0x0407
        assert not window.volume_check.isChecked()

        # the bass engine is a read-only table, empty until asked
        assert window.bass_read_btn.isEnabled()       # capture advertises 0x012C
        assert window.bass_table.columnCount() == 4
        assert window.bass_table.rowCount() == 0

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

        # the Expert view is live but empty, and explains the guard before use
        assert window.expert_view.isEnabled()
        assert window.raw_table.rowCount() == 0
        assert window.lbl_raw.text() == "Nothing sent yet."
        assert window.raw_command_edit.text() == ""
        assert window.raw_payload_edit.text() == ""
        assert window.raw_type_edit.text() == "0"
    finally:
        window.close()


def test_raw_console_refuses_the_guarded_families() -> None:
    """The guard is the point of this tab, so it gets its own test."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()

        # typing a guarded id says so before anything is clicked
        for text, needle in (("0x0415", "hearing"), ("0x0302", "OTA"),
                             ("0x0f00", "debug")):
            window.raw_command_edit.setText(text)
            assert "will refuse" in window.lbl_raw_command.text()
            assert needle in window.lbl_raw_command.text()

        # typing an ordinary id names its manager, command and gating
        window.raw_command_edit.setText("0x0404")
        assert "setNoiseReduction" in window.lbl_raw_command.text()
        assert "set" in window.lbl_raw_command.text()
        assert "will refuse" not in window.lbl_raw_command.text()

        # sending one is refused, and the dialog explains why -- with no override
        window.raw_command_edit.setText("0x0415")
        window.raw_payload_edit.setText("01")
        # connecting already wrote frames, so compare against that, not zero
        written = len(window.controller.transport.writes)
        with _Modal() as modal:
            window._send_raw()
        assert len(modal.warnings) == 1
        assert modal.warnings[0][0] == "Refused"
        assert "hearing" in modal.warnings[0][1]
        assert "tlcli send --force" in modal.warnings[0][1]
        assert modal.questions == [], "a refused command must not even ask"
        assert window.raw_table.rowCount() == 0
        assert len(window.controller.transport.writes) == written
    finally:
        window.close()


def test_raw_console_sends_only_after_a_confirmation() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        window.raw_command_edit.setText("0x010C")
        window.raw_payload_edit.setText("01")

        # declining the confirmation must not put anything on the wire
        written = len(window.controller.transport.writes)
        with _Modal(answer=False) as modal:
            window._send_raw()
        assert len(modal.questions) == 1
        assert "Send 0x010C" in modal.questions[0][1]
        assert window.raw_table.rowCount() == 0
        assert len(window.controller.transport.writes) == written

        # a bad payload is reported instead of being sent
        window.raw_payload_edit.setText("012")
        with _Modal() as modal:
            window._send_raw()
        assert modal.warnings and modal.warnings[0][0] == "Check the command"
        assert "even number of digits" in modal.warnings[0][1]
    finally:
        window.close()


def test_raw_console_renders_a_send_and_its_replies() -> None:
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_raw_result({
            "cmd": 0x010C, "payload": b"\x01", "type_byte": 0, "listen": 1.0,
            "frames": [b"\xaa"], "packets": [],
            "events": [
                {"cmd": "0x810C", "name": "getNoiseReduction", "payload": "00010101"},
                {"cmd": "0x0204", "name": "notificationEvent", "event": "battery",
                 "payload": "0103016402640300", "tlv": {1: 100, 2: 100, 3: 0}},
            ]})
        assert window.raw_table.rowCount() == 3
        # the tx row sits above its replies and the block reads top to bottom
        assert window.raw_table.item(0, 1).text() == "tx"
        assert window.raw_table.item(1, 1).text() == "rx"
        assert window.raw_table.item(2, 1).text() == "rx"
        assert "0x010C" in window.raw_table.item(0, 3).text()
        assert "1 frame(s)" in window.raw_table.item(0, 3).text()
        assert "1=100" in window.raw_table.item(2, 3).text()
        assert "2 packet(s) heard" in window.lbl_raw.text()

        # no reply is reported as such, not as silence
        window._show_raw_result({"cmd": 0x0400, "payload": b"", "type_byte": 0,
                                 "listen": 0.5, "frames": [b"\xaa"], "packets": [],
                                 "events": []})
        assert window.raw_table.rowCount() == 4
        assert "no reply" in window.lbl_raw.text()

        window._clear_raw()
        assert window.raw_table.rowCount() == 0
        assert window.lbl_raw.text() == "Nothing sent yet."
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
        assert not window.bass_read_btn.isEnabled()

        window.controller.supports = lambda cmd: True
        window._apply_capability_gating()
        assert window.noise_apply_btn.isEnabled()
        assert window.eq_apply_btn.isEnabled()
        assert window.volume_apply_btn.isEnabled()
        assert window.bass_read_btn.isEnabled()
    finally:
        window.close()


def test_bass_table_renders_signed_bands() -> None:
    """The bass read is display-only, and its values are signed."""
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_bass({"status": 0, "count": 3, "entries": [
            {"min": -5, "max": 5, "current": 0, "type": 1},
            {"min": -5, "max": 5, "current": -3, "type": 2},
            {"min": -5, "max": 5, "current": 0, "type": None},
        ]})
        assert window.bass_table.rowCount() == 3
        assert window.bass_table.item(0, 0).text() == "type 1"
        assert window.bass_table.item(0, 1).text() == "-5"   # signed, not 251
        assert window.bass_table.item(0, 2).text() == "+5"
        assert window.bass_table.item(1, 3).text() == "-3"
        # an absent type byte shows a dash rather than an invented label
        assert window.bass_table.item(2, 0).text() == "\u2014"
        assert "3 bass band(s)" in window.statusBar().currentMessage()

        # re-reading replaces the table instead of appending
        window._show_bass({"status": 0, "count": 1, "entries": [
            {"min": -5, "max": 5, "current": 0, "type": 1}]})
        assert window.bass_table.rowCount() == 1
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
        path.write_text(f"{999_999_999}\ntlapp\n{socket.gethostname()}\n", encoding="utf-8")
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


def test_default_lock_path_honours_the_override(monkeypatch, tmp_path) -> None:
    """OPEN_REAL_LINK_LOCK redirects the lock, creating its parent."""
    from tlapp.instance import LOCK_PATH_ENV, default_lock_path

    target = tmp_path / "nested" / "app.lock"
    monkeypatch.setenv(LOCK_PATH_ENV, str(target))
    assert default_lock_path() == target
    assert target.parent.is_dir()


def test_default_lock_path_falls_back_when_the_cache_dir_is_empty(
        monkeypatch, tmp_path) -> None:
    """An empty GenericCacheLocation must fall through, not build a bare path."""
    from PySide6.QtCore import QStandardPaths

    from tlapp.instance import LOCK_PATH_ENV, default_lock_path

    monkeypatch.setattr(QStandardPaths, "writableLocation",
                        staticmethod(lambda _loc: ""))
    target = tmp_path / "app.lock"
    monkeypatch.setenv(LOCK_PATH_ENV, str(target))
    assert default_lock_path() == target


def test_instance_lock_is_a_context_manager(tmp_path) -> None:
    from tlapp.instance import InstanceLock

    path = tmp_path / "app.lock"
    with InstanceLock(path) as lock:
        assert lock.acquired
    # the lock is released on exit, so a fresh instance acquires it
    again = InstanceLock(path)
    try:
        assert again.acquired
    finally:
        again.release()


def test_explain_without_a_holder(tmp_path) -> None:
    """When QLockFile cannot name a holder, the message must still be useful."""
    from tlapp.instance import InstanceLock

    lock = InstanceLock(tmp_path / "app.lock")
    try:
        lock._lock.getLockInfo = lambda: (0, "", "")
        message = lock.explain()
        assert "already running" in message
        assert "pid" not in message
    finally:
        lock.release()


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
        # Sound, Gestures, Backup, Events and the Expert view all need a device
        assert window.connect_btn.text() == "Connect"
        assert not window.tabs.isTabEnabled(TAB_SOUND)
        assert not window.tabs.isTabEnabled(TAB_GESTURES)
        assert not window.tabs.isTabEnabled(TAB_BACKUP)
        assert not window.tabs.isTabEnabled(TAB_EVENTS)
        assert not window.expert_view.isEnabled()
        assert not window.view_buttons[VIEW_EXPERT].isEnabled()
        assert window.views.currentIndex() == VIEW_GENERAL
        assert not window.compat_check_btn.isEnabled()
        assert "Not connected" in window.compat_label.text()
    finally:
        window.close()


def _stub_find(window, *, ok: bool = True):
    """Record find_buds calls instead of putting them on the wire.

    The wire for 0x0400 is covered exactly in tests/test_app.py; what matters
    here is the window's own logic -- the confirmation, the button state, and
    the stop on exit -- so the controller is stubbed rather than replayed.
    """
    calls: list[bool] = []

    def find_buds(on: bool) -> dict:
        calls.append(bool(on))
        return {"requested": bool(on), "status": 0 if ok else 0x0E, "ok": ok}

    window.controller.find_buds = find_buds
    return calls


def test_find_buds_rings_confirms_and_stops() -> None:
    """Ring is confirmed (it is loud); Stop deliberately is not."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        calls = _stub_find(window)
        assert window.find_btn.isEnabled()           # capture advertises 0x0400
        assert not window.find_stop_btn.isEnabled()  # nothing is ringing yet

        # declining the confirmation must not send anything
        with _Modal(answer=False) as modal:
            window._ring_buds()
        assert len(modal.questions) == 1
        assert "loud" in modal.questions[0][1]
        assert calls == []
        assert window.find_btn.isEnabled()

        # confirming sends 0x0400 01 and flips the buttons
        with _Modal() as modal:
            window._ring_buds()
        assert modal.warnings == []
        assert calls == [True]
        assert window._ringing
        assert not window.find_btn.isEnabled()
        assert window.find_stop_btn.isEnabled()

        # Stop is one click: no question, just the write
        with _Modal() as modal:
            window._stop_ringing()
        assert modal.questions == []
        assert calls == [True, False]
        assert not window._ringing
        assert window.find_btn.isEnabled()
        assert not window.find_stop_btn.isEnabled()
    finally:
        window.close()


def test_disconnect_and_close_send_the_stop() -> None:
    """A ringing headset must not be left ringing by an exit.

    The live run that verified 0x0400 sent the stop from a ``finally``; the
    app's two exits are disconnect and close, so both do the same.
    """
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    window.connect_device()
    app.processEvents()
    calls = _stub_find(window)
    with _Modal():
        window._ring_buds()
    assert window._ringing

    window._toggle_connection()          # disconnect while ringing
    app.processEvents()
    assert not window._ringing
    assert calls == [True, False]        # the stop went out before the link went down
    assert not window.controller.connected
    assert window.find_btn.isEnabled() is False   # disconnected now

    # and the close path, from a fresh ringing state
    window2 = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    window2.connect_device()
    app.processEvents()
    calls2 = _stub_find(window2)
    with _Modal():
        window2._ring_buds()
    assert window2._ringing
    window2.close()
    assert calls2 == [True, False]


def test_a_failed_ring_does_not_claim_to_be_ringing() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        calls = _stub_find(window, ok=False)
        with _Modal() as modal:
            window._ring_buds()
        assert calls == [True]
        assert modal.warnings and modal.warnings[0][0] == "Ring refused"
        assert "0x0E" in modal.warnings[0][1]
        assert not window._ringing
        assert window.find_btn.isEnabled()
    finally:
        window.close()


def test_paired_devices_render() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        assert window.pair_read_btn.isEnabled()      # capture advertises 0x0112
        assert window.pair_table.rowCount() == 0     # not read until asked

        window._show_paired({"status": 0, "count": 2, "entries": [
            {"mac": "aa:bb:cc:dd:ee:ff", "name": "Phone", "state": 2, "flag": 1},
            {"mac": "11:22:33:44:55:66", "name": "", "state": 0, "flag": 0},
        ]})
        assert window.pair_table.rowCount() == 2
        assert window.pair_table.item(0, 0).text() == "Phone"
        assert window.pair_table.item(0, 1).text() == "aa:bb:cc:dd:ee:ff"
        assert window.pair_table.item(0, 2).text() == "2"
        assert window.pair_table.item(1, 0).text() == "—"   # unnamed entry
        assert "2 paired device(s), 1 active" in window.lbl_paired.text()
        assert "host's own Bluetooth names" in window.lbl_paired.text()

        # re-reading replaces rather than appends
        window._show_paired({"status": 0, "count": 0, "entries": []})
        assert window.pair_table.rowCount() == 0
        assert "0 paired device(s)" in window.lbl_paired.text()
    finally:
        window.close()


def test_find_and_paired_controls_are_capability_gated() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        assert window.pair_read_btn.isEnabled()

        window.controller.supports = lambda cmd: False
        window._apply_capability_gating()
        assert not window.find_btn.isEnabled()
        assert not window.find_stop_btn.isEnabled()
        assert not window.pair_read_btn.isEnabled()

        window.controller.supports = lambda cmd: True
        window._apply_capability_gating()
        assert window.find_btn.isEnabled()       # not ringing, so Ring is live
        assert not window.find_stop_btn.isEnabled()
        assert window.pair_read_btn.isEnabled()
    finally:
        window.close()


def test_find_buttons_are_disabled_while_disconnected() -> None:
    """Unlike the rest of the Device tab: Ring is loud, so it waits for a link."""
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        assert not window.controller.connected
        assert not window.find_btn.isEnabled()
        assert not window.find_stop_btn.isEnabled()
    finally:
        window.close()


def test_expert_view_collects_the_diagnostics() -> None:
    """The expert surface is its own view, and General keeps the tabs simple."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()

        assert window.tabs.count() == 5
        assert window.expert_view.isEnabled()

        expert = window.expert_view
        device = window.tabs.widget(TAB_DEVICE)
        sound = window.tabs.widget(TAB_SOUND)

        # everything diagnostic lives on the Expert view
        for widget in (window.caps_table, window.bass_table,
                       window.bass_read_btn, window.diag_table,
                       window.raw_table, window.raw_send_btn):
            assert expert.isAncestorOf(widget), widget

        # and left the everyday tabs alone
        assert not device.isAncestorOf(window.caps_table)
        assert not sound.isAncestorOf(window.bass_table)
        assert not sound.isAncestorOf(window.bass_read_btn)

        # the capability table is still populated the moment we connect
        assert window.caps_table.rowCount() == len(t.ALL_COMMANDS)
        assert len(window._diag_buttons) == 6
    finally:
        window.close()


def test_two_views_switch_and_lock_offline() -> None:
    """General and Expert are top-level views; Expert needs a device."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        assert window.views.currentIndex() == VIEW_GENERAL
        # Expert is locked while disconnected, so switching to it is refused
        assert not window.expert_view.isEnabled()
        window.show_view(VIEW_EXPERT)
        assert window.views.currentIndex() == VIEW_GENERAL

        window.connect_device()
        app.processEvents()
        assert window.expert_view.isEnabled()
        assert window.view_buttons[VIEW_EXPERT].isEnabled()
        window.show_view(VIEW_EXPERT)
        assert window.views.currentIndex() == VIEW_EXPERT
        assert window.view_buttons[VIEW_EXPERT].isChecked()

        # disconnecting falls back to General and re-locks Expert
        window._toggle_connection()
        app.processEvents()
        assert window.views.currentIndex() == VIEW_GENERAL
        assert not window.expert_view.isEnabled()
    finally:
        window.close()


def test_compat_banner_verdicts() -> None:
    """The banner must name a partial unit rather than hide the gap."""
    from tlapp.controller import assess_compatibility

    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_compat(assess_compatibility(None, None))
        assert window._compat["verdict"] == "unsupported"
        assert "0x8100" in window._compat["headline"]
        assert window.compat_details_btn.isEnabled()

        window._show_compat(assess_compatibility(
            {"ok": True, "commands": {0x0105, 0x0106, 0x0109, 0x010C, 0x0404}},
            {"model": {"model_id": 0x064C12}}))
        assert window._compat["verdict"] == "limited"
        assert "Equalizer" in window._compat["headline"]
        assert "does not advertise" in window.compat_label.text()

        window._show_compat(None)
        assert "Not connected" in window.compat_label.text()
        assert not window.compat_details_btn.isEnabled()
    finally:
        window.close()


def test_diag_renderer_handles_every_shape() -> None:
    """One renderer for every read: nothing is dressed up as a control."""
    _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window._show_diag("Example (0x0000)", {
            "status": 0, "flag": True, "off": False,
            "entries": [1, 2, 3], "empty": [], "text": "0.0.0.1",
        })
        rows = {window.diag_table.item(r, 0).text():
                window.diag_table.item(r, 1).text()
                for r in range(window.diag_table.rowCount())}
        assert rows["Read"] == "Example (0x0000)"
        assert rows["status"] == "0x00 (0)"
        assert rows["flag"] == "yes"
        assert rows["off"] == "no"
        assert rows["entries"] == "3 record(s)"
        assert rows["empty"] == "none"
        assert rows["text"] == "0.0.0.1"
    finally:
        window.close()


def test_read_switch_features_reports_the_empty_finding() -> None:
    """An empty list is the finding on this unit, so it must be said, not blank."""
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        window.controller.switch_features = lambda: {"status": 0, "entries": []}
        window._read_switch_features()
        assert window.diag_table.rowCount() == 1
        assert "none" in window.diag_table.item(0, 1).text()
        assert "empty list" in window.diag_table.item(0, 1).text()

        window.controller.switch_features = lambda: {
            "status": 0, "entries": [{"feature": 0x11, "enabled": 1},
                                     {"feature": 0x12, "enabled": 0}]}
        window._read_switch_features()
        assert window.diag_table.rowCount() == 2
        assert window.diag_table.item(0, 0).text() == "Feature 0x11"
        assert window.diag_table.item(0, 1).text() == "enabled"
        assert window.diag_table.item(1, 1).text() == "disabled"
    finally:
        window.close()


def test_diag_buttons_are_capability_gated() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        assert all(b.isEnabled() for b in window._diag_buttons)

        window.controller.supports = lambda cmd: False
        window._apply_capability_gating()
        for button, cmd in window._diag_buttons.items():
            spec = t.command_spec(cmd)
            gated = spec is not None and spec.gating.startswith("cap-bit")
            assert button.isEnabled() is not gated, (button.text(), hex(cmd))

        window.controller.supports = lambda cmd: True
        window._apply_capability_gating()
        assert all(b.isEnabled() for b in window._diag_buttons)

        # and nothing is live while disconnected, ungated or not
        window.controller.disconnect()
        window._set_connected(False)
        window._apply_capability_gating()
        assert not any(b.isEnabled() for b in window._diag_buttons)
    finally:
        window.close()


def test_an_ungated_read_is_not_switched_off_by_the_bitmap() -> None:
    """Regression: 0x0200 is ungated, so it is absent from the bitmap.

    Asking ``supports()`` about an always-on or ungated command therefore says
    "no" for a command that plainly works -- which is exactly how the
    notification-capabilities read got disabled the first time.
    """
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        assert not window.controller.supports(0x0200)   # not in the bitmap
        assert window._capability_enabled(0x0200)       # yet it is usable
        assert window.controller.supports(0x0107) is not False
        assert window._capability_enabled(0x0107)
        # a cap-bit command really does follow the bitmap
        window.controller.supports = lambda cmd: False
        assert not window._capability_enabled(0x0107)
        assert window._capability_enabled(0x0200)
    finally:
        window.close()


def test_read_identity_renders_the_three_reads() -> None:
    app = _app()
    window = MainWindow(ConnectionOptions(replay="spp-smoketest.jsonl"))
    try:
        window.connect_device()
        app.processEvents()
        window.controller.identity = lambda: {
            "vendor_id": {"status": 0, "vendor_id": 0x072F},
            "protocol_version": {"status": 0, "data": "000001"},
            "color_id": {"status": 0, "color_id": 6},
        }
        window._read_identity()
        rows = {window.diag_table.item(r, 0).text():
                window.diag_table.item(r, 1).text()
                for r in range(window.diag_table.rowCount())}
        assert rows["Vendor id"] == "0x072F"
        assert rows["Protocol version"] == "000001"
        assert rows["Colour id"] == "0x06"
    finally:
        window.close()


# --------------------------------------------------------------- bluetooth gate
def test_gate_accepts_as_soon_as_the_radio_is_on(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    import tlapp.bluetooth as bt
    from tlapp.gate import BluetoothGate

    _app()
    monkeypatch.setattr(bt, "bluetooth_state", lambda: bt.ON)
    gate = BluetoothGate()
    gate._check()
    assert gate.result() == QDialog.DialogCode.Accepted


def test_gate_stays_open_while_the_radio_is_off(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    import tlapp.bluetooth as bt
    from tlapp.gate import BluetoothGate

    _app()
    monkeypatch.setattr(bt, "bluetooth_state", lambda: bt.OFF)
    gate = BluetoothGate()
    gate._check()
    assert gate.result() != QDialog.DialogCode.Accepted


def test_gate_turn_on_reports_that_it_is_waiting(monkeypatch) -> None:
    import tlapp.bluetooth as bt
    from tlapp.gate import BluetoothGate

    _app()
    monkeypatch.setattr(bt, "set_bluetooth", lambda on=True: True)
    gate = BluetoothGate()
    gate._try_turn_on()
    assert "Turning Bluetooth on" in gate.status.text()


def test_gate_turn_on_falls_back_to_system_settings(monkeypatch) -> None:
    import tlapp.bluetooth as bt
    from tlapp.gate import BluetoothGate

    _app()
    opened = []
    monkeypatch.setattr(bt, "set_bluetooth", lambda on=True: False)
    monkeypatch.setattr(bt, "open_bluetooth_settings",
                        lambda: opened.append(True) or True)
    gate = BluetoothGate()
    gate._try_turn_on()
    assert opened == [True]
    assert "Open Bluetooth settings" in gate.status.text()


def test_gate_settings_button_sets_a_waiting_message(monkeypatch) -> None:
    import tlapp.bluetooth as bt
    from tlapp.gate import BluetoothGate

    _app()
    monkeypatch.setattr(bt, "open_bluetooth_settings", lambda: True)
    gate = BluetoothGate()
    gate._open_settings()
    assert "Waiting for Bluetooth" in gate.status.text()
