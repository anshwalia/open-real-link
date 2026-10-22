"""The PySide6 main window.

Widgets only: every device action is delegated to
:class:`tlapp.controller.DeviceController`, and every failure becomes a message
box. The widgets never touch the wire, and the controller never imports Qt.

The app is built feature by feature: connect + capability view, device status,
noise mode get/set, and the equalizer / high-volume settings. The tone / OTA /
debug families have no control here at all, and neither does any operation that
has not been verified on hardware -- those are added one at a time as they are
built and live-tested.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

from .controller import (
    ConnectionOptions,
    ControllerError,
    DeviceController,
    eq_preset_choices,
    noise_choices,
)

#: Sentinel so ``_run`` can tell "returned None" from "failed".
_FAILED = object()

TAB_DEVICE = 0
TAB_SOUND = 1


class MainWindow(QMainWindow):
    """The application window: a connection bar and two tabs."""

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

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.controller.disconnect()
        super().closeEvent(event)


def mode_name(value: int) -> str:
    """Display name for a noise-mode value."""
    return t.NOISE_MODE_NAMES.get(value, f"0x{value:02X}")


def seconds_phrase(seconds: int) -> str:
    """Format a use-time value as ``Hh Mm``."""
    total = int(seconds)
    return f"{total // 3600}h {(total % 3600) // 60}m"
