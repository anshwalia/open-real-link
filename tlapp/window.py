"""The PySide6 main window.

Widgets only: every device action is delegated to
:class:`tlapp.controller.DeviceController`, and every failure becomes a message
box. The widgets never touch the wire, and the controller never imports Qt.

The app is built feature by feature: connect + capability view, device status,
noise mode get/set, the equalizer / high-volume settings, settings
backup / restore, the push-event monitor, the gesture table, and a guarded raw
console. The tone / OTA / debug families have no control here at all -- the raw
console refuses them -- and neither does any operation that has not been verified
on hardware; those are added one at a time as they are built and live-tested.
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
    SEND_LISTEN_SECONDS,
    ConnectionOptions,
    ControllerError,
    DeviceController,
    eq_preset_choices,
    event_phrase,
    function_choices,
    noise_choices,
    parse_command,
    parse_payload,
)

#: Sentinel so ``_run`` can tell "returned None" from "failed".
_FAILED = object()

TAB_DEVICE = 0
TAB_SOUND = 1
TAB_GESTURES = 2
TAB_BACKUP = 3
TAB_EVENTS = 4
TAB_RAW = 5

#: How often the event monitor reads the link, in milliseconds.  Slightly
#: longer than the read budget so the GUI thread always gets a turn.
EVENT_POLL_MS = 60


class MainWindow(QMainWindow):
    """The application window: a connection bar and six tabs."""

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
        self.tabs.addTab(self._build_gestures_tab(), "Gestures")
        self.tabs.addTab(self._build_backup_tab(), "Backup")
        self.tabs.addTab(self._build_events_tab(), "Events")
        self.tabs.addTab(self._build_raw_tab(), "Raw")
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

        # -- find my buds --------------------------------------------------
        find_box = QGroupBox("Find my buds")
        find_row = QHBoxLayout(find_box)
        self._ringing = False
        self.find_btn = QPushButton("Ring")
        self.find_btn.clicked.connect(self._ring_buds)
        self.find_stop_btn = QPushButton("Stop")
        self.find_stop_btn.clicked.connect(self._stop_ringing)
        self.find_stop_btn.setEnabled(False)
        find_row.addWidget(self.find_btn)
        find_row.addWidget(self.find_stop_btn)
        find_row.addWidget(QLabel(
            "Rings loudly. There is no read-back for <code>0x0400</code>, so the "
            "buttons show what was last sent."), 1)
        outer.addWidget(find_box)

        # -- paired devices ------------------------------------------------
        pair_box = QGroupBox("Connected devices")
        pair_outer = QVBoxLayout(pair_box)
        pair_row = QHBoxLayout()
        self.pair_read_btn = QPushButton("Read")
        self.pair_read_btn.clicked.connect(
            lambda: self._run(lambda c: c.paired_devices(), self._show_paired))
        pair_row.addWidget(self.pair_read_btn)
        self.lbl_paired = QLabel("Not read yet.")
        self.lbl_paired.setWordWrap(True)
        pair_row.addWidget(self.lbl_paired, 1)
        pair_outer.addLayout(pair_row)
        self.pair_table = self._table(["Name", "Address", "State", "Flag"])
        self.pair_table.setMaximumHeight(130)
        pair_outer.addWidget(self.pair_table)
        outer.addWidget(pair_box)

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

        # -- bass engine (read-only) ---------------------------------------
        bass_box = QGroupBox("Bass engine")
        bass_outer = QVBoxLayout(bass_box)
        bass_row = QHBoxLayout()
        self.bass_read_btn = QPushButton("Read")
        self.bass_read_btn.clicked.connect(
            lambda: self._run(lambda c: c.bass_engine(), self._show_bass))
        bass_row.addWidget(self.bass_read_btn)
        bass_note = QLabel(
            "Read-only (<code>0x012C</code>). The <code>0x041B</code> write is "
            "accepted, but which band its <code>type</code> byte selects is not "
            "verified, so the app does not offer it.")
        bass_note.setWordWrap(True)
        bass_row.addWidget(bass_note, 1)
        bass_outer.addLayout(bass_row)
        self.bass_table = self._table(["Type", "Min", "Max", "Current"])
        self.bass_table.setMaximumHeight(110)
        bass_outer.addWidget(self.bass_table)
        outer.addWidget(bass_box)
        outer.addStretch(1)
        return page

    # ----------------------------------------------------------- gestures tab
    def _build_gestures_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        note = QLabel(
            "Each row binds one gesture on one earbud to a function. The write "
            "(<code>0x0401</code>) replaces the <b>whole</b> table, so the app "
            "reads it, applies only the rows you changed, writes every row back "
            "and reads it again. <b>Off</b> (function 0) disables a gesture. "
            "Function names are the app's own enum — the unit's table proves the "
            "encoding — and the read-back is the last word on whether it stuck.")
        note.setWordWrap(True)
        outer.addWidget(note)

        row = QHBoxLayout()
        self.gestures_read_btn = QPushButton("Read table")
        self.gestures_read_btn.clicked.connect(
            lambda: self._run(lambda c: c.gestures(), self._show_gestures))
        self.long_press_btn = QPushButton("Disable long press")
        self.long_press_btn.clicked.connect(self._stage_long_press_off)
        self.gestures_revert_btn = QPushButton("Revert")
        self.gestures_revert_btn.clicked.connect(self._revert_gestures)
        self.gestures_apply_btn = QPushButton("Apply changes")
        self.gestures_apply_btn.clicked.connect(self._apply_gestures)
        for widget in (self.gestures_read_btn, self.long_press_btn,
                       self.gestures_revert_btn, self.gestures_apply_btn):
            row.addWidget(widget)
        row.addStretch(1)
        outer.addLayout(row)

        self.lbl_gestures = QLabel("No table read yet.")
        self.lbl_gestures.setWordWrap(True)
        outer.addWidget(self.lbl_gestures)

        self.gestures_table = self._table(
            ["Device", "Button", "Gesture", "Function", "Was"])
        outer.addWidget(self.gestures_table, 1)
        #: The table exactly as the device last reported it -- the baseline every
        #: edit is diffed against, so Apply can never write a half-built table.
        self._gesture_entries: list = []
        self._gestures_writable = False
        self._gestures_readable = False
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

    # -------------------------------------------------------------- raw tab
    def _build_raw_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        note = QLabel(
            "Send one command and show the frames and every reply. This is the "
            "only place a command without a verified UI can be driven, so it is "
            "also where the guard lives: the <b>hearing / ear-scan tones</b>, "
            "<b>OTA firmware push</b> and <b>debug log</b> families are refused "
            "outright. There is no override here — <code>tlcli send --force</code> "
            "is the documented escape hatch. Never sweep unknown command space "
            "against a worn headset.")
        note.setWordWrap(True)
        outer.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("Command"))
        self.raw_command_edit = QLineEdit()
        self.raw_command_edit.setPlaceholderText("0x010C")
        self.raw_command_edit.setMaximumWidth(110)
        self.raw_command_edit.textChanged.connect(self._raw_command_changed)
        row.addWidget(self.raw_command_edit)
        row.addWidget(QLabel("Payload"))
        self.raw_payload_edit = QLineEdit()
        self.raw_payload_edit.setPlaceholderText("01 01 20  (hex; empty is fine)")
        row.addWidget(self.raw_payload_edit, 1)
        row.addWidget(QLabel("Type"))
        self.raw_type_edit = QLineEdit("0")
        self.raw_type_edit.setMaximumWidth(50)
        row.addWidget(self.raw_type_edit)
        row.addWidget(QLabel("Listen"))
        self.raw_listen_edit = QLineEdit(f"{SEND_LISTEN_SECONDS:g}")
        self.raw_listen_edit.setMaximumWidth(50)
        row.addWidget(self.raw_listen_edit)
        row.addWidget(QLabel("s"))
        self.raw_send_btn = QPushButton("Send")
        self.raw_send_btn.clicked.connect(self._send_raw)
        row.addWidget(self.raw_send_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_raw)
        row.addWidget(clear_btn)
        outer.addLayout(row)

        self.lbl_raw_command = QLabel("e.g. 0x010C")
        self.lbl_raw_command.setWordWrap(True)
        outer.addWidget(self.lbl_raw_command)

        self.lbl_raw = QLabel("Nothing sent yet.")
        self.lbl_raw.setWordWrap(True)
        outer.addWidget(self.lbl_raw)

        self.raw_table = self._table(["Time", "Dir", "Command", "Detail"])
        outer.addWidget(self.raw_table, 1)
        #: Whether the buds are in-ear, from the last status read.  The console
        #: warns on this rather than refusing, because a single command the user
        #: typed is not a sweep -- but it must never be a surprise either.
        self._wear: bool | None = None
        return page

    # ------------------------------------------------------------- connection
    def connect_device(self) -> None:
        """Public entry point: connect now (used by ``--connect`` / ``--replay``)."""
        if not self.controller.connected:
            self._toggle_connection()

    def _toggle_connection(self) -> None:
        if self.controller.connected:
            self._stop_ringing_quietly()
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
        self.tabs.setTabEnabled(TAB_GESTURES, state)
        self.tabs.setTabEnabled(TAB_BACKUP, state)
        self.tabs.setTabEnabled(TAB_EVENTS, state)
        self.tabs.setTabEnabled(TAB_RAW, state)
        if not state:
            self._stop_listening()
            self._wear = None
            self._set_ringing(False)

    def _apply_capability_gating(self) -> None:
        """Gray out controls the unit says it does not take."""
        self.noise_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_NOISE_REDUCTION))
        self.eq_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_EQ_MODE))
        self.volume_apply_btn.setEnabled(self.controller.supports(t.CMD_SET_HIGH_VOLUME))
        self.bass_read_btn.setEnabled(
            self.controller.supports(t.CMD_GET_MULTI_BASS_ENGINE))
        self._gestures_readable = self.controller.supports(t.CMD_GET_KEY_FUNCTION)
        self._gestures_writable = self.controller.supports(t.CMD_SET_KEY_FUNCTION)
        self.gestures_read_btn.setEnabled(self._gestures_readable)
        self.long_press_btn.setEnabled(self._gestures_writable)
        self.pair_read_btn.setEnabled(
            self.controller.supports(t.CMD_GET_MULTI_CONNECT))
        self._set_ringing(self._ringing)
        self._gesture_edited()

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

    # ------------------------------------------------------- find my buds
    def _ring_buds(self) -> None:
        if QMessageBox.question(
                self, "Ring the buds",
                "Ring the buds to find them?\n\nThis plays a loud tone until you "
                "press Stop or the headset gives up."
        ) != QMessageBox.StandardButton.Yes:
            return
        result = self._run(lambda c: c.find_buds(True))
        if result is _FAILED or result is None:
            return
        if not result["ok"]:
            # There is no read pair for 0x0400, so the ack is the only signal
            # there is.  Do not claim the buds are ringing on a bad one.
            self._set_ringing(False)
            self._warn("Ring refused",
                       f"The device answered status 0x{result['status']:02X}, so "
                       "the buds are probably not ringing. 0x0400 has no read "
                       "back, so this ack is all the device gives.")
            return
        self._set_ringing(True)
        self.statusBar().showMessage("Ringing. Press Stop when you have them.")

    def _stop_ringing(self) -> None:
        # Deliberately unconfirmed.  If the buds are ringing in a bag, the one
        # moment you want this to be instant is now.
        result = self._run(lambda c: c.find_buds(False))
        if result is _FAILED or result is None:
            return
        self._set_ringing(False)
        if result["ok"]:
            self.statusBar().showMessage("Stop sent.")
        else:
            self.statusBar().showMessage(
                f"Stop sent, but the device answered 0x{result['status']:02X}.")

    def _stop_ringing_quietly(self) -> None:
        """Send ``0x0400 00`` if we believe the buds are ringing, ignoring errors.

        The live run that verified this command sent the stop from a ``finally``
        for exactly this reason: a lost ack or a crash must not leave the buds
        ringing in a drawer.  Disconnect and close are the app's two exits, so
        both call this.
        """
        if not getattr(self, "_ringing", False) or not self.controller.connected:
            return
        try:
            self.controller.find_buds(False)
        except ControllerError:
            pass  # best effort: the link may already be gone
        self._set_ringing(False)

    def _set_ringing(self, ringing: bool) -> None:
        """Reflect what was last *sent* -- ``0x0400`` has no read pair to ask.

        Also gated on the live connection, unlike the rest of the Device tab: a
        Ring button that stays clickable while disconnected invites a confusing
        warning for the one control here that makes a noise.
        """
        self._ringing = bool(ringing)
        usable = self.controller.connected and self.controller.supports(
            t.CMD_SET_FIND_MODE)
        self.find_btn.setEnabled(usable and not self._ringing)
        self.find_stop_btn.setEnabled(usable and self._ringing)

    # ------------------------------------------------------ paired devices
    def _show_paired(self, info: dict) -> None:
        self.pair_table.setRowCount(0)
        for entry in info["entries"]:
            self._add_row(self.pair_table, entry["name"] or "—", entry["mac"],
                          str(entry["state"]), str(entry["flag"]))
        active = sum(1 for entry in info["entries"] if entry["state"])
        self.lbl_paired.setText(
            f"{info['count']} paired device(s), {active} active. "
            "The names are this host's own Bluetooth names.")
        self.statusBar().showMessage(
            f"{info['count']} paired device(s) reported by the headset.")

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
        self._wear = bool(wear["in_ear"])
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

    def _show_bass(self, info: dict) -> None:
        """Fill the bass table from a ``0x012C`` read.

        The values are signed, so a range prints as ``-5..+5``; the type column
        shows the device's own band selector rather than an invented name.
        """
        self.bass_table.setRowCount(0)
        for entry in info["entries"]:
            band_type = entry["type"]
            self._add_row(
                self.bass_table,
                f"type {band_type}" if band_type is not None else "—",
                f"{entry['min']:+d}",
                f"{entry['max']:+d}",
                f"{entry['current']:+d}")
        self.statusBar().showMessage(
            f"{info['count']} bass band(s) reported by the device.")

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

    # ------------------------------------------------------------ raw console
    def _raw_command_changed(self) -> None:
        """Say what the typed command is -- and whether it is refused -- live."""
        try:
            cmd = parse_command(self.raw_command_edit.text())
        except ControllerError as exc:
            self.lbl_raw_command.setText(str(exc))
            self.lbl_raw_command.setStyleSheet("color: #a00;")
            return
        family = t.dangerous_family(cmd)
        if family is not None:
            self.lbl_raw_command.setText(
                f"0x{cmd:04X} — {family}: the app will refuse this.")
            self.lbl_raw_command.setStyleSheet("color: #a00; font-weight: 600;")
            return
        spec = t.command_spec(cmd)
        manager = t.CLASS_NAMES.get(t.cmd_class(cmd), "unknown manager")
        name = t.CMD_NAMES.get(cmd) or (spec.name if spec else "not a known command")
        gating = f" · {spec.gating}" if spec else ""
        self.lbl_raw_command.setText(f"0x{cmd:04X} — {manager} · {name}{gating}")
        self.lbl_raw_command.setStyleSheet("")

    def _send_raw(self) -> None:
        try:
            cmd = parse_command(self.raw_command_edit.text())
            payload = parse_payload(self.raw_payload_edit.text())
            type_byte = int(self.raw_type_edit.text().strip() or "0", 0)
            listen = float(self.raw_listen_edit.text().strip() or SEND_LISTEN_SECONDS)
        except ControllerError as exc:
            self._warn("Check the command", str(exc))
            return
        except ValueError as exc:
            self._warn("Check the numbers", f"Type and Listen must be numbers: {exc}")
            return
        if not 0 <= type_byte <= 0xFF:
            self._warn("Check the numbers", "Type must be one byte (0..255).")
            return

        family = t.dangerous_family(cmd)
        if family is not None:
            self._warn(
                "Refused",
                f"0x{cmd:04X} is part of the {family}, which is deliberately not "
                "implemented.\n\nThe app will not send it. `tlcli send --force` is "
                "the documented escape hatch for someone who has read the "
                "protocol notes and knows why they want it.")
            return

        name = t.CMD_NAMES.get(cmd, "")
        lines = [f"Send 0x{cmd:04X}" + (f" ({name})" if name else ""),
                 f"Payload: {payload.hex(' ') or '(none)'}   "
                 f"type {type_byte}   listening {listen:g}s", ""]
        if self._wear:
            lines.append("The buds are WORN. Never sweep unknown command space "
                         "against a worn headset.")
        elif self._wear is False:
            lines.append("The buds are not being worn.")
        if QMessageBox.question(self, "Send a raw command", "\n".join(lines)) \
                != QMessageBox.StandardButton.Yes:
            return

        result = self._run(lambda c: c.send_raw(cmd, payload, type_byte=type_byte,
                                                listen=listen))
        if result is _FAILED or result is None:
            return
        self._show_raw_result(result)

    def _show_raw_result(self, result: dict) -> None:
        # replies first, so that after inserting at the top the tx row sits
        # above its own replies and the block reads top-to-bottom
        for row in reversed(result["events"]):
            self._add_raw_row("rx", row.get("name") or row["cmd"], event_phrase(row))
        self._add_raw_row(
            "tx", t.CMD_NAMES.get(result["cmd"], "") or f"0x{result['cmd']:04X}",
            f"0x{result['cmd']:04X}  [{result['payload'].hex(' ')}]  "
            f"type {result['type_byte']}  {len(result['frames'])} frame(s)")
        replies = len(result["events"])
        self.lbl_raw.setText(
            f"0x{result['cmd']:04X} sent in {len(result['frames'])} frame(s); "
            f"{replies} packet(s) heard in {result['listen']:g}s."
            + ("" if replies else "  (no reply — the command may take none, or the "
                                  "unit may not take it at all)"))

    def _add_raw_row(self, direction: str, command: str, detail: str) -> None:
        self.raw_table.insertRow(0)
        cells = (QTime.currentTime().toString("HH:mm:ss.zzz"), direction,
                 command, detail)
        for column, value in enumerate(cells):
            self.raw_table.setItem(0, column, QTableWidgetItem(value))
        self.raw_table.resizeColumnToContents(0)

    def _clear_raw(self) -> None:
        self.raw_table.setRowCount(0)
        self.lbl_raw.setText("Nothing sent yet.")

    # ------------------------------------------------------ gesture table
    def _show_gestures(self, table: dict) -> None:
        """Fill the table from a ``0x0108`` read; this is the write baseline."""
        self._gesture_entries = list(table["entries"])
        self.gestures_table.setRowCount(0)
        for entry in self._gesture_entries:
            row = self.gestures_table.rowCount()
            self.gestures_table.insertRow(row)
            self.gestures_table.setItem(row, 0, QTableWidgetItem(
                t.DEVICE_TYPE_NAMES.get(entry.device_type, str(entry.device_type))))
            self.gestures_table.setItem(row, 1, QTableWidgetItem(str(entry.button)))
            self.gestures_table.setItem(row, 2, QTableWidgetItem(
                t.ACTION_NAMES.get(entry.action, f"0x{entry.action:02X}")))

            combo = QComboBox()
            for value, label in function_choices():
                combo.addItem(label, value)
            index = combo.findData(entry.function)
            if index < 0:
                # A function this build has no name for must stay selectable and
                # unchanged -- silently rewriting it to `off` would be a bug that
                # disables the user's gesture the moment they touched anything.
                combo.addItem(f"0x{entry.function:02X} (unnamed)", entry.function)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._gesture_edited)
            self.gestures_table.setCellWidget(row, 3, combo)

            self.gestures_table.setItem(row, 4, QTableWidgetItem(
                t.FUNCTION_NAMES.get(entry.function, f"0x{entry.function:02X}")))
        self._gesture_edited()
        if not self._gesture_entries:
            self.lbl_gestures.setText("The device reported an empty gesture table.")

    def _gesture_updates(self) -> dict:
        """``{(device, button, action): function}`` for the rows that changed."""
        updates = {}
        for row, entry in enumerate(self._gesture_entries):
            combo = self.gestures_table.cellWidget(row, 3)
            if combo is None:
                continue
            value = combo.currentData()
            if value != entry.function:
                updates[(entry.device_type, entry.button, entry.action)] = value
        return updates

    def _gesture_edited(self) -> None:
        updates = self._gesture_updates()
        self.gestures_apply_btn.setEnabled(self._gestures_writable and bool(updates))
        if not updates:
            if self._gesture_entries:
                self.lbl_gestures.setText(
                    f"{len(self._gesture_entries)} gesture(s); no unsaved changes.")
            return
        self.lbl_gestures.setText(f"{len(updates)} unsaved change(s): " + ", ".join(
            f"{t.DEVICE_TYPE_NAMES.get(device, device)} "
            f"{t.ACTION_NAMES.get(action, action)} → "
            f"{t.FUNCTION_NAMES.get(value, f'0x{value:02X}')}"
            for (device, _button, action), value in sorted(updates.items())))

    def _stage_long_press_off(self) -> None:
        """Stage — not write — every long-press row at ``off``.

        Staging keeps one write path and one confirmation: the user reviews the
        whole table and clicks Apply.
        """
        if not self._gesture_entries:
            self._warn("No table", "Read the gesture table first.")
            return
        staged = 0
        for row, entry in enumerate(self._gesture_entries):
            if entry.action not in t.LONG_PRESS_ACTIONS:
                continue
            combo = self.gestures_table.cellWidget(row, 3)
            index = combo.findData(0)
            if index >= 0 and combo.currentIndex() != index:
                combo.setCurrentIndex(index)
                staged += 1
        self.statusBar().showMessage(
            f"Staged long press off for {staged} gesture(s); click Apply to write.")

    def _revert_gestures(self) -> None:
        """Put the combos back to the table last read from the device."""
        if not self._gesture_entries:
            return
        self._show_gestures({"entries": self._gesture_entries})
        self.statusBar().showMessage("Reverted to the table last read from the device.")

    def _apply_gestures(self) -> None:
        updates = self._gesture_updates()
        if not updates:
            return
        if QMessageBox.question(
                self, "Write the gesture table",
                f"Write {len(updates)} change(s) to the headset?\n\n"
                "0x0401 replaces the whole table, so the app re-reads it, applies "
                "these rows, and writes every row back."
        ) != QMessageBox.StandardButton.Yes:
            return
        result = self._run(lambda c: c.set_gestures(updates))
        if result is _FAILED or result is None:
            return
        self._show_gestures(result["table"])
        if not result["changed"]:
            self.statusBar().showMessage("No change needed; the table already matched.")
        elif result["verified"]:
            self.statusBar().showMessage(
                f"Wrote {len(result['changed'])} change(s) and read them back.")
        else:
            self._warn("Read-back mismatch",
                       "The device accepted the write but read back a different "
                       "table. Re-read it before making more changes.")

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
        self._stop_ringing_quietly()
        self.controller.disconnect()
        super().closeEvent(event)


def mode_name(value: int) -> str:
    """Display name for a noise-mode value."""
    return t.NOISE_MODE_NAMES.get(value, f"0x{value:02X}")


def seconds_phrase(seconds: int) -> str:
    """Format a use-time value as ``Hh Mm``."""
    total = int(seconds)
    return f"{total // 3600}h {(total % 3600) // 60}m"
