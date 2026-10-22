"""The PySide6 main window.

Widgets only: every device action is delegated to
:class:`tlapp.controller.DeviceController`, and every failure becomes a message
box. The widgets never touch the wire, and the controller never imports Qt.

The app is built feature by feature: connect + capability view, device status,
noise mode get/set, the equalizer / high-volume settings, settings
backup / restore, and the push-event monitor. The tone / OTA / debug families
have no control here at all, and neither does any operation that has not been
verified on hardware -- those are added one at a time as they are built and
live-tested.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import tlcore as t

from . import backupfile
from .controller import (
    EVENT_POLL_SECONDS,
    ConnectionOptions,
    ControllerError,
    DeviceController,
    eq_preset_choices,
    event_phrase,
    noise_choices,
)

#: Sentinel so ``_run`` can tell "returned None" from "failed".
_FAILED = object()

TAB_DEVICE = 0
TAB_SOUND = 1
TAB_BACKUP = 2
TAB_EVENTS = 3

#: How often the event monitor reads the link, in milliseconds.  Slightly
#: longer than the read budget so the GUI thread always gets a turn.
EVENT_POLL_MS = 60


class MainWindow(QMainWindow):
    """The application window: a connection bar and four tabs."""

    def __init__(self, options: ConnectionOptions):
        super().__init__()
        self.options = options
        self.controller = DeviceController(options)
        self.setWindowTitle("OpenRealLink")
        self.resize(880, 640)
        self._build_toolbar()
        self._build_tabs()
        self._set_connected(False)
        self.statusBar().showMessage(
            f"Ready (replay: {options.replay})." if options.replay
            else "Ready. Enter a device address and connect.")

    # ------------------------------------------------------------------ chrome
    def _build_toolbar(self) -> None:
        bar = QToolBar("Connection")
        bar.setMovable(False)
        self.addToolBar(bar)
        bar.addWidget(QLabel("  Device  "))
        self.address_edit = QLineEdit(self.options.address or "")
        self.address_edit.setPlaceholderText("AA-BB-CC-DD-EE-FF")
        self.address_edit.setMinimumWidth(200)
        if self.options.replay:
            self.address_edit.setEnabled(False)
        bar.addWidget(self.address_edit)
        self.reset_check = QCheckBox("reset link")
        if self.options.replay:
            self.reset_check.setEnabled(False)
        bar.addWidget(self.reset_check)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._toggle_connection)
        bar.addWidget(self.connect_btn)
        bar.addWidget(QLabel("   "))
        self.conn_label = QLabel("disconnected")
        self.conn_label.setStyleSheet("color: #a00; font-weight: 600;")
        bar.addWidget(self.conn_label)

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_device_tab(), "Device")
        self.tabs.addTab(self._build_sound_tab(), "Sound")
        self.tabs.addTab(self._build_backup_tab(), "Backup")
        self.tabs.addTab(self._build_events_tab(), "Events")
        self.setCentralWidget(self.tabs)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        return table

    # ------------------------------------------------------------- device tab
    def _build_device_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        info_box = QGroupBox("Device")
        form = QFormLayout(info_box)
        self.lbl_model = QLabel("—")
        self.lbl_firmware = QLabel("—")
        self.lbl_version = QLabel("—")
        self.lbl_battery = QLabel("—")
        self.lbl_wear = QLabel("—")
        self.lbl_noise = QLabel("—")
        self.lbl_use_time = QLabel("—")
        self.lbl_caps = QLabel("—")
        for label, widget in (
            ("Model", self.lbl_model), ("Firmware", self.lbl_firmware),
            ("Version", self.lbl_version), ("Battery", self.lbl_battery),
            ("Wear", self.lbl_wear), ("Noise mode", self.lbl_noise),
            ("Use time", self.lbl_use_time), ("Capabilities", self.lbl_caps),
        ):
            form.addRow(label, widget)
        outer.addWidget(info_box)

        buttons = QHBoxLayout()
        status_btn = QPushButton("Read status")
        status_btn.clicked.connect(lambda: self._run(lambda c: c.status(), self._show_status))
        caps_btn = QPushButton("Read capabilities")
        caps_btn.clicked.connect(
            lambda: self._run(lambda c: c.capabilities(), self._show_caps))
        buttons.addWidget(status_btn)
        buttons.addWidget(caps_btn)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        outer.addWidget(QLabel("Capability bitmap (0x8100) — every known command"))
        self.caps_table = self._table(["Command", "Manager", "Name", "Advertised"])
        self.caps_table.setColumnWidth(0, 80)
        outer.addWidget(self.caps_table, 1)
        return page

    # -------------------------------------------------------------- sound tab
    def _build_sound_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        note = QLabel(
            "A noise-mode write is accepted only while the buds are <b>worn</b>; "
            "otherwise the device answers <code>0x8404 0e</code>. Apply reads the "
            "mode back, because the asynchronous push can arrive out of order.")
        note.setWordWrap(True)
        outer.addWidget(note)

        box = QGroupBox("Active noise mode")
        row = QHBoxLayout(box)
        self.noise_combo = QComboBox()
        for value, label in noise_choices():
            self.noise_combo.addItem(f"{label}  (0x{value:02X})", value)
        read_btn = QPushButton("Read")
        read_btn.clicked.connect(lambda: self._run(lambda c: c.noise_mode(), self._show_noise))
        self.noise_apply_btn = QPushButton("Apply")
        self.noise_apply_btn.clicked.connect(self._apply_noise)
        row.addWidget(self.noise_combo, 1)
        row.addWidget(read_btn)
        row.addWidget(self.noise_apply_btn)
        outer.addWidget(box)

        # -- equalizer -----------------------------------------------------
        eq_box = QGroupBox("Equalizer (sound-effect preset)")
        eq_outer = QVBoxLayout(eq_box)
        eq_row = QHBoxLayout()
        self.eq_combo = QComboBox()
        for value, label in eq_preset_choices():
            self.eq_combo.addItem(f"{label}  (0x{value:02X})", value)
        eq_read = QPushButton("Read")
        eq_read.clicked.connect(lambda: self._run(lambda c: c.eq_mode(), self._show_eq))
        self.eq_apply_btn = QPushButton("Apply")
        self.eq_apply_btn.clicked.connect(self._apply_eq)
        eq_row.addWidget(self.eq_combo, 1)
        eq_row.addWidget(eq_read)
        eq_row.addWidget(self.eq_apply_btn)
        eq_outer.addLayout(eq_row)
        self.lbl_eq = QLabel("—")
        eq_outer.addWidget(self.lbl_eq)
        presets_row = QHBoxLayout()
        presets_btn = QPushButton("Read presets")
        presets_btn.clicked.connect(
            lambda: self._run(lambda c: c.eq_presets(), self._show_eq_presets))
        presets_row.addWidget(presets_btn)
        presets_row.addWidget(QLabel("the device's own 0x0122 preset / custom-EQ list"), 1)
        eq_outer.addLayout(presets_row)
        self.eq_table = self._table(["Selected", "eqId", "min", "max", "bands", "name"])
        self.eq_table.setMaximumHeight(120)
        eq_outer.addWidget(self.eq_table)
        outer.addWidget(eq_box)

        # -- high volume ---------------------------------------------------
        vol_box = QGroupBox("High volume (loudness boost)")
        vol_row = QHBoxLayout(vol_box)
        self.volume_check = QCheckBox("enabled")
        vol_read = QPushButton("Read")
        vol_read.clicked.connect(
            lambda: self._run(lambda c: c.high_volume(), self._show_high_volume))
        self.volume_apply_btn = QPushButton("Apply")
        self.volume_apply_btn.clicked.connect(self._apply_high_volume)
        vol_row.addWidget(self.volume_check)
        vol_row.addWidget(vol_read)
        vol_row.addWidget(self.volume_apply_btn)
        vol_row.addStretch(1)
        outer.addWidget(vol_box)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------- backup tab
    def _build_backup_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        note = QLabel(
            "A backup polls every setting the unit advertises and stores each raw "
            "reply, plus the capability bitmap and identity, as one plain JSON "
            "file. Restore re-applies only the settings whose read and write "
            "forms are both recovered; the rest are reported, never guessed at. "
            "<b>A snapshot is personal data</b> — if the unit supports "
            "<code>0x0112</code> it embeds your paired-device names. Redact "
            "before sharing.")
        note.setWordWrap(True)
        outer.addWidget(note)

        # -- capture -------------------------------------------------------
        save_box = QGroupBox("Back up this device")
        save_outer = QVBoxLayout(save_box)
        save_row = QHBoxLayout()
        self.backup_path_edit = QLineEdit()
        self.backup_path_edit.setPlaceholderText(
            str(backupfile.default_backup_dir() / "backup.json"))
        browse_btn = QPushButton("Save as…")
        browse_btn.clicked.connect(self._choose_save_path)
        self.backup_btn = QPushButton("Back up now")
        self.backup_btn.clicked.connect(self._do_backup)
        save_row.addWidget(self.backup_path_edit, 1)
        save_row.addWidget(browse_btn)
        save_row.addWidget(self.backup_btn)
        save_outer.addLayout(save_row)
        self.lbl_backup = QLabel("—")
        self.lbl_backup.setWordWrap(True)
        save_outer.addWidget(self.lbl_backup)
        self.backup_table = self._table(["Setting", "In the snapshot"])
        self.backup_table.setMaximumHeight(140)
        save_outer.addWidget(self.backup_table)
        outer.addWidget(save_box)

        # -- restore -------------------------------------------------------
        load_box = QGroupBox("Restore from a file")
        load_outer = QVBoxLayout(load_box)
        load_row = QHBoxLayout()
        self.restore_path_edit = QLineEdit()
        self.restore_path_edit.setPlaceholderText("path to a .json backup")
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._choose_open_path)
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.clicked.connect(self._preview_restore)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self._do_restore)
        load_row.addWidget(self.restore_path_edit, 1)
        load_row.addWidget(open_btn)
        load_row.addWidget(self.preview_btn)
        load_row.addWidget(self.restore_btn)
        load_outer.addLayout(load_row)
        self.lbl_restore = QLabel("Preview first: it reports what a restore would "
                                  "do without writing anything.")
        self.lbl_restore.setWordWrap(True)
        load_outer.addWidget(self.lbl_restore)
        self.restore_table = self._table(["Setting", "Result"])
        self.restore_table.setMaximumHeight(140)
        load_outer.addWidget(self.restore_table)
        outer.addWidget(load_box)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------- events tab
    def _build_events_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        note = QLabel(
            "The device pushes state changes on its own: button presses, battery "
            "and wear updates, noise-mode and multi-connect reports. Nothing is "
            "sent while listening — the app only reads. Listening holds the link "
            "in short slices, so leave it off when you are not watching.")
        note.setWordWrap(True)
        outer.addWidget(note)

        row = QHBoxLayout()
        self.listen_btn = QPushButton("Start listening")
        self.listen_btn.clicked.connect(self._toggle_listening)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_events)
        self.event_types_btn = QPushButton("Read event types")
        self.event_types_btn.clicked.connect(
            lambda: self._run(lambda c: c.event_types(), self._show_event_types))
        row.addWidget(self.listen_btn)
        row.addWidget(clear_btn)
        row.addWidget(self.event_types_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.lbl_events = QLabel("Not listening.")
        self.lbl_events.setWordWrap(True)
        outer.addWidget(self.lbl_events)

        self.events_table = self._table(["Time", "Event", "Detail"])
        outer.addWidget(self.events_table, 1)

        # Poll on a timer rather than in a thread: the transport has one reader,
        # and a background reader would need a lock over every other operation.
        self.events_timer = QTimer(self)
        self.events_timer.setInterval(EVENT_POLL_MS)
        self.events_timer.timeout.connect(self._poll_events)
        return page

    # ------------------------------------------------------------- connection
    def connect_device(self) -> None:
        """Public entry point: connect now (used by ``--connect`` / ``--replay``)."""
        if not self.controller.connected:
            self._toggle_connection()

    def _toggle_connection(self) -> None:
        if self.controller.connected:
            self.controller.disconnect()
            self._set_connected(False)
            self.statusBar().showMessage("Disconnected.")
            return
        if not self.options.replay:
            self.options.address = self.address_edit.text().strip() or None
            if not self.options.address:
                self._warn("Address needed",
                           "Enter the headset's Bluetooth address (e.g. "
                           "AA-BB-CC-DD-EE-FF), or start the app with --replay.")
                return
        self.options.reset = self.reset_check.isChecked()
        self._run(lambda c: c.connect(), self._after_connect, require=False)

    def _after_connect(self, _result=None) -> None:
        self._set_connected(True)
        self.statusBar().showMessage("Connected. Reading capabilities and status…")
        self._run(lambda c: c.capabilities(), self._show_caps)
        self._run(lambda c: c.status(), self._show_status)
        self._apply_capability_gating()
        self.statusBar().showMessage("Connected.")

    def _set_connected(self, state: bool) -> None:
        self.connect_btn.setText("Disconnect" if state else "Connect")
        self.conn_label.setText("connected" if state else "disconnected")
        self.conn_label.setStyleSheet(
            "color: #080; font-weight: 600;" if state else "color: #a00; font-weight: 600;")
        self.tabs.setTabEnabled(TAB_SOUND, state)
        self.tabs.setTabEnabled(TAB_BACKUP, state)
        self.tabs.setTabEnabled(TAB_EVENTS, state)
        if not state:
            self._stop_listening()

    def _apply_capability_gating(self) -> None:
        """Gray out controls the unit says it does not take."""
        self.noise_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_NOISE_REDUCTION))
        self.eq_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_EQ_MODE))
        self.volume_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_HIGH_VOLUME))

    # ----------------------------------------------------------------- runner
    def _run(self, work, done=None, *, require: bool = True, busy: bool = True):
        """Run a controller call behind a wait cursor, reporting failures."""
        if require and not self.controller.connected:
            self._warn("Not connected", "Connect to a device first.")
            return None
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
        try:
            result = work(self.controller)
        except ControllerError as exc:
            self._warn("Device error", str(exc))
            result = _FAILED
        except Exception as exc:  # noqa: BLE001 - surface every failure, never crash the UI
            self._warn("Unexpected error", f"{type(exc).__name__}: {exc}")
            result = _FAILED
        finally:
            if busy:
                QApplication.restoreOverrideCursor()
        if result is not _FAILED and done is not None:
            done(result)
        return result

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    @staticmethod
    def _add_row(table: QTableWidget, *values: str) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))

    # -------------------------------------------------------------- show data
    def _show_caps(self, caps: dict) -> None:
        advertised = set(caps["commands"])
        self.lbl_caps.setText(
            f"{len(advertised)} commands advertised (status={caps['status']}, "
            f"{len(caps['bits'])} capability bits set)")
        self.caps_table.setRowCount(0)
        for spec in t.ALL_COMMANDS:
            row = self.caps_table.rowCount()
            self.caps_table.insertRow(row)
            self.caps_table.setItem(row, 0, QTableWidgetItem(f"0x{spec.cmd:04X}"))
            self.caps_table.setItem(
                row, 1, QTableWidgetItem(t.CLASS_NAMES.get(t.cmd_class(spec.cmd), spec.family)))
            self.caps_table.setItem(row, 2, QTableWidgetItem(spec.name))
            mark = ""
            if spec.cmd in advertised:
                mark = "always-on" if spec.cmd in t.ALWAYS_ON else "yes"
            self.caps_table.setItem(row, 3, QTableWidgetItem(mark))

    def _show_status(self, status: dict) -> None:
        model = status["model"]["model_id"]
        if model is None:
            self.lbl_model.setText("unknown")
        else:
            spec = t.device_spec(f"{model:06X}")
            self.lbl_model.setText(
                f"{spec.name} (0x{model:06X})" if spec else f"0x{model:06X}")
        self.lbl_firmware.setText(", ".join(status["firmware"]["versions"]) or "unknown")
        version = status.get("version", {}).get("value")
        self.lbl_version.setText(f"{version} (0x{version:04X})" if version else "unknown")
        battery = status["battery"]["devices"]
        self.lbl_battery.setText("   ".join(
            f"{dev}: {d['level']}%{' (charging)' if d['charging'] else ''}"
            for dev, d in battery.items()) or "unknown")
        wear = status["wear"]
        self.lbl_wear.setText("in-ear" if wear["in_ear"] else f"not worn  {wear['devices']}")
        mode = status["noise_mode"]
        self._show_noise(mode)
        use = status["use_time"]["devices"]
        self.lbl_use_time.setText("   ".join(
            f"{dev}: {seconds_phrase(sec)}" for dev, sec in use.items()) or "unknown")

    def _show_noise(self, mode: int) -> None:
        self.lbl_noise.setText(f"{mode_name(mode)}  (0x{mode:02X})")
        index = self.noise_combo.findData(mode)
        if index >= 0:
            self.noise_combo.setCurrentIndex(index)

    def _show_eq(self, mode: int) -> None:
        self.lbl_eq.setText(f"active preset {mode}  (0x{mode:02X})")
        index = self.eq_combo.findData(mode)
        if index >= 0:
            self.eq_combo.setCurrentIndex(index)

    def _show_eq_presets(self, info: dict) -> None:
        self.eq_table.setRowCount(0)
        for entry in info["entries"]:
            row = self.eq_table.rowCount()
            self.eq_table.insertRow(row)
            self.eq_table.setItem(
                row, 0, QTableWidgetItem("yes" if entry["is_selected"] else ""))
            self.eq_table.setItem(row, 1, QTableWidgetItem(str(entry["eq_id"])))
            self.eq_table.setItem(row, 2, QTableWidgetItem(str(entry["min"])))
            self.eq_table.setItem(row, 3, QTableWidgetItem(str(entry["max"])))
            self.eq_table.setItem(row, 4, QTableWidgetItem(str(len(entry["points"]))))
            self.eq_table.setItem(row, 5, QTableWidgetItem(entry["name"] or "—"))
        self.statusBar().showMessage(
            f"{info['count']} preset record(s) reported by the device.")

    def _show_high_volume(self, enabled: bool) -> None:
        self.volume_check.setChecked(bool(enabled))

    # ------------------------------------------------------------ noise write
    def _apply_noise(self) -> None:
        value = self.noise_combo.currentData()
        result = self._run(lambda c: c.set_noise_mode(value))
        if result is _FAILED or result is None:
            return
        self._show_noise(result["actual"])
        if result["ok"]:
            self.statusBar().showMessage(f"Noise mode set to {mode_name(value)}.")
        else:
            self._warn("Mode mismatch",
                       f"Wrote 0x{value:02X} but read back 0x{result['actual']:02X}. "
                       "The asynchronous push can reorder; the read is authoritative.")

    # -------------------------------------------------------------- eq write
    def _apply_eq(self) -> None:
        value = self.eq_combo.currentData()
        result = self._run(lambda c: c.set_eq_mode(value))
        if result is _FAILED or result is None:
            return
        self._show_eq(result["actual"])
        if result["ok"]:
            self.statusBar().showMessage(f"Equalizer set to preset {value}.")
        else:
            self._warn("Preset mismatch",
                       f"Wrote preset {value} but read back {result['actual']}. "
                       "The device clamps anything outside 0..6; the read is "
                       "authoritative.")

    def _apply_high_volume(self) -> None:
        wanted = self.volume_check.isChecked()
        result = self._run(lambda c: c.set_high_volume(wanted))
        if result is _FAILED or result is None:
            return
        self._show_high_volume(result["actual"])
        if result["ok"]:
            self.statusBar().showMessage(
                f"High volume {'on' if wanted else 'off'}.")
        else:
            self._warn("Setting mismatch",
                       f"Wrote high volume {'on' if wanted else 'off'} but read "
                       f"back {'on' if result['actual'] else 'off'}; the read is "
                       "authoritative.")

    # --------------------------------------------------------- event monitor
    def _toggle_listening(self) -> None:
        if self.events_timer.isActive():
            self._stop_listening()
            self.statusBar().showMessage("Stopped listening for device pushes.")
            return
        if not self.controller.connected:
            self._warn("Not connected", "Connect to a device first.")
            return
        self.events_timer.start()
        self.listen_btn.setText("Stop listening")
        self.lbl_events.setText("Listening for device pushes…")
        self.statusBar().showMessage("Listening for device pushes…")

    def _stop_listening(self) -> None:
        self.events_timer.stop()
        self.listen_btn.setText("Start listening")
        if self.lbl_events.text().startswith("Listening"):
            self.lbl_events.setText(
                f"Not listening. {self.events_table.rowCount()} event(s) captured.")

    def _clear_events(self) -> None:
        self.events_table.setRowCount(0)
        self.lbl_events.setText(
            "Listening for device pushes…" if self.events_timer.isActive()
            else "Not listening.")

    def _poll_events(self) -> None:
        """One timer tick: drain whatever the device pushed.

        Deliberately **not** routed through :meth:`_run`: that puts up a wait
        cursor and a modal on failure, and a modal opened from a timer callback
        spins a nested event loop.  A read failure stops listening and reports
        in the status bar instead.
        """
        try:
            rows = self.controller.poll_events(EVENT_POLL_SECONDS)
        except ControllerError as exc:
            self._stop_listening()
            self.lbl_events.setText(f"Listening stopped: {exc}")
            self.statusBar().showMessage(f"Listening stopped: {exc}")
            return
        for row in rows:
            self._add_event_row(row)
        if rows:
            total = self.events_table.rowCount()
            self.lbl_events.setText(f"Listening… {total} event(s) captured.")

    def _add_event_row(self, row: dict) -> None:
        self.events_table.insertRow(0)  # newest first
        cells = (QTime.currentTime().toString("HH:mm:ss.zzz"),
                 row.get("event") or row.get("name") or row["cmd"],
                 event_phrase(row))
        for column, value in enumerate(cells):
            self.events_table.setItem(0, column, QTableWidgetItem(value))
        self.events_table.resizeColumnToContents(0)

    def _show_event_types(self, caps: dict) -> None:
        names = ", ".join(f"0x{kind:02X}" for kind in caps["types"])
        self.statusBar().showMessage(
            f"This unit pushes {caps['count']} event type(s): {names or 'none'}.")

    # ------------------------------------------------------- backup / restore
    def _choose_save_path(self) -> str | None:
        start = (self.backup_path_edit.text().strip()
                 or str(backupfile.default_backup_dir() / "backup.json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save backup", start, "JSON files (*.json)")
        if path:
            self.backup_path_edit.setText(path)
        return path or None

    def _choose_open_path(self) -> str | None:
        start = (self.restore_path_edit.text().strip()
                 or str(backupfile.default_backup_dir()))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open backup", start, "JSON files (*.json)")
        if path:
            self.restore_path_edit.setText(path)
        return path or None

    def _do_backup(self) -> None:
        path = self.backup_path_edit.text().strip() or self._choose_save_path()
        if not path:
            return
        result = self._run(lambda c: c.backup_to(path))
        if result is _FAILED or result is None:
            return
        written, summary = result
        # the file we just wrote is the obvious thing to restore from
        self.backup_path_edit.setText(str(written))
        self.restore_path_edit.setText(str(written))
        self._show_backup_summary(summary)
        self.statusBar().showMessage(f"Backup written to {written}.")

    def _show_backup_summary(self, summary: dict) -> None:
        firmware = ", ".join(summary["firmware"]) or "unknown"
        self.lbl_backup.setText(
            f"{summary['model'] or 'unknown model'} · firmware {firmware} · "
            f"{summary['captured']} response(s) captured, {summary['skipped']} "
            f"skipped · {len(summary['restorable'])} restorable")
        self.backup_table.setRowCount(0)
        for key in summary["restorable"]:
            self._add_row(self.backup_table, key, "restorable")
        for key in summary["captured_only"]:
            extra = (" — may hold paired-device names"
                     if key == "multi_point" and summary["contains_paired_devices"] else "")
            self._add_row(self.backup_table, key, f"captured, no restorer{extra}")

    def _preview_restore(self) -> None:
        path = self.restore_path_edit.text().strip() or self._choose_open_path()
        if not path:
            return
        report = self._run(lambda c: c.preview_restore(path))
        if report is _FAILED or report is None:
            return
        self._show_restore_report(report)
        self.statusBar().showMessage(
            "Preview only — nothing was written to the device.")

    def _do_restore(self) -> None:
        path = self.restore_path_edit.text().strip() or self._choose_open_path()
        if not path:
            return
        if QMessageBox.question(
                self, "Restore settings",
                f"Re-apply the settings from\n{path}\nto the connected device?"
        ) != QMessageBox.StandardButton.Yes:
            return
        report = self._run(lambda c: c.restore_from(path))
        if report is _FAILED or report is None:
            return
        self._show_restore_report(report)
        applied = len(report["applied"])
        self.statusBar().showMessage(
            f"Restored {applied} setting(s); "
            f"{len(report['not_restored'])} left alone.")

    def _show_restore_report(self, report: dict) -> None:
        self.restore_table.setRowCount(0)
        for item in report["applied"]:
            self._add_row(self.restore_table, item["setting"],
                          "would apply (dry run)" if item.get("dry_run") else "applied")
        for item in report["not_restored"]:
            self._add_row(self.restore_table, item["setting"], item["reason"])
        if not report["applied"] and not report["not_restored"]:
            self._add_row(self.restore_table, "(nothing)", "the file carries no settings")
        self.lbl_restore.setText(
            f"{len(report['applied'])} restorable, "
            f"{len(report['not_restored'])} reported as not restorable.")

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.events_timer.stop()
        self.controller.disconnect()
        super().closeEvent(event)


def mode_name(value: int) -> str:
    """Display name for a noise-mode value."""
    return t.NOISE_MODE_NAMES.get(value, f"0x{value:02X}")


def seconds_phrase(seconds: int) -> str:
    """Format a use-time value as ``Hh Mm``."""
    total = int(seconds)
    return f"{total // 3600}h {(total % 3600) // 60}m"
