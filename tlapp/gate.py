"""The pre-flight screen shown when the host's Bluetooth radio is off.

``app.main`` shows this before the main window, and only when
:func:`tlapp.bluetooth.bluetooth_state` says the radio is **off** (never when it
is ``unknown``).  ``exec()`` returns ``Accepted`` once Bluetooth is on -- whether
the app switched it or the user did in System Settings -- and ``Rejected`` if
they quit, in which case the app exits without opening a window.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import bluetooth, theme

#: How often the gate looks for the radio coming up, in milliseconds.  Slow
#: enough to be free, fast enough that flipping the switch in System Settings
#: dismisses the gate almost at once.
POLL_MS = 1200

__all__ = ["BluetoothGate", "POLL_MS"]


class BluetoothGate(QDialog):
    """A modal screen that blocks until Bluetooth is on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("OpenRealLink — Bluetooth is off")
        self.setModal(True)
        self.setMinimumWidth(480)

        outer = QVBoxLayout(self)
        margin = theme.PAGE_MARGIN + 8
        outer.setContentsMargins(margin, margin, margin, margin)
        outer.setSpacing(theme.PAGE_SPACING)

        heading = QLabel("Bluetooth is off")
        theme.set_role(heading, "heading")
        outer.addWidget(heading)

        body = QLabel(
            "OpenRealLink talks to your headset over Bluetooth, and Bluetooth "
            "is switched off on this computer.")
        body.setWordWrap(True)
        outer.addWidget(body)

        actions = QHBoxLayout()
        actions.setSpacing(theme.PAGE_SPACING)
        self.turn_on_btn = QPushButton("Turn on Bluetooth")
        self.turn_on_btn.clicked.connect(self._try_turn_on)
        theme.set_role(self.turn_on_btn, theme.PRIMARY)
        actions.addWidget(self.turn_on_btn)
        self.settings_btn = QPushButton("Open Bluetooth settings")
        self.settings_btn.clicked.connect(self._open_settings)
        actions.addWidget(self.settings_btn)
        actions.addStretch(1)
        outer.addLayout(actions)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        theme.set_role(self.status, "muted")
        outer.addWidget(self.status)

        footer = QHBoxLayout()
        self.check_btn = QPushButton("Check again")
        self.check_btn.clicked.connect(self._check)
        footer.addWidget(self.check_btn)
        footer.addStretch(1)
        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.reject)
        theme.set_role(self.quit_btn, theme.GHOST)
        footer.addWidget(self.quit_btn)
        outer.addLayout(footer)

        # Watch for the radio coming up on its own, so the user can flip the
        # system switch and have the gate dismiss itself.
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._check)
        self._timer.start()

    # -- actions -----------------------------------------------------------
    def _try_turn_on(self) -> None:
        """Fire the OS switch; the poll picks up the result (never blocks)."""
        if bluetooth.set_bluetooth(True):
            self.status.setText("Turning Bluetooth on…")
        else:
            self.status.setText(
                "This system does not let the app switch Bluetooth on — use "
                "Open Bluetooth settings.")
            self._open_settings()

    def _open_settings(self) -> None:
        bluetooth.open_bluetooth_settings()
        if not self.status.text():
            self.status.setText("Waiting for Bluetooth to come on…")

    def _check(self) -> None:
        """Accept as soon as the radio is on; called by the button and the poll."""
        if bluetooth.bluetooth_state() == bluetooth.ON:
            self.accept()

    def done(self, result: int) -> None:  # noqa: D102 (Qt override)
        self._timer.stop()
        super().done(result)
