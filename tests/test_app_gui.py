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
from tlapp.window import TAB_BACKUP, TAB_SOUND, MainWindow  # noqa: E402


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
        # Sound and Backup are disabled until a device is connected; Device stays usable
        assert window.connect_btn.text() == "Connect"
        assert not window.tabs.isTabEnabled(TAB_SOUND)
        assert not window.tabs.isTabEnabled(TAB_BACKUP)
    finally:
        window.close()
