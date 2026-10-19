"""A Qt-free controller over the verified core, for the desktop app.

The window owns widgets; this class owns the device. Keeping it free of Qt is
what makes the app testable: every operation runs against a recorded fixture
with no display and no hardware, and ``import tlapp`` never needs PySide6.

    from tlapp import ConnectionOptions, DeviceController
    ctrl = DeviceController(ConnectionOptions(replay="spp-smoketest.jsonl"))
    ctrl.connect()
    ctrl.capabilities()
    ctrl.status()

Every operation is **synchronous**: one call is a handful of round trips at
~10 ms each, so the window runs them on the GUI thread behind a wait cursor.

This file implements the first slice of the app only -- connect + capability
view, device status, and noise mode get/set. Later features (EQ, gestures,
events, backup, voice, console) are added here as they are built, so the app
never carries a control for something that is not yet wired up.
"""
from __future__ import annotations

from dataclasses import dataclass

import tlcore as t
from tlcli.cli import noise_name, open_session

__all__ = [
    "ConnectionOptions",
    "ControllerError",
    "DeviceController",
    "friendly_error",
    "noise_choices",
]


@dataclass
class ConnectionOptions:
    """Everything :func:`tlcli.cli.open_session` needs, as a plain object.

    Mirrors the CLI's shared options so the app and ``tlcli`` open a device the
    exact same way (including ``replay``, which needs no radio).
    """

    address: str | None = None
    channel: int | None = None
    uuid: str | None = None
    transport: str = "auto"
    replay: str | None = None
    reset: bool = False
    loose: bool = False
    frame_size: int = 512
    interval_ms: int = 10
    timeout: float = 1.5


class ControllerError(Exception):
    """A failure already phrased for a person to read."""


def friendly_error(exc: Exception) -> str:
    """Turn a core exception into one sentence a person can act on."""
    if isinstance(exc, t.NotWorn):
        return "The buds are not being worn. Put them in and try again."
    if isinstance(exc, t.NotConnected):
        return "The device disconnected. Reconnect and try again."
    if isinstance(exc, t.Timeout):
        return f"The device did not answer ({exc}). It may be busy; try again."
    if isinstance(exc, t.DeviceRejected):
        status = getattr(exc, "status", None)
        if status is not None:
            return (f"The device rejected the command (status 0x{status:02X}). "
                    "A noise-mode write is accepted only while the buds are worn.")
    return str(exc) or type(exc).__name__


def noise_choices() -> list[tuple[int, str]]:
    """The selectable noise modes, in wire-value order."""
    order = (
        t.NOISE_MODE_NORMAL,
        t.NOISE_MODE_TRANSPARENCY,
        t.NOISE_MODE_ANC_LIGHT,
        t.NOISE_MODE_ANC_DEEP,
        t.NOISE_MODE_ANC_MEDIUM,
        t.NOISE_MODE_ANC_SMART,
    )
    return [(value, noise_name(value)) for value in order]


class DeviceController:
    """Owns one device connection and exposes the defined operations."""

    def __init__(self, options: ConnectionOptions | None = None):
        self.options = options or ConnectionOptions()
        self.transport = None
        self.session = None
        self._caps: dict | None = None

    # -- lifecycle ---------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.session is not None and bool(self.transport.is_connected())

    @property
    def replay(self) -> bool:
        return bool(self.options.replay)

    def connect(self) -> None:
        """Open the transport. Cheap; call :meth:`capabilities` to negotiate."""
        if self.connected:
            return
        self.disconnect()
        try:
            self.transport, self.session = open_session(self.options)
        except t.TLError as exc:
            raise ControllerError(friendly_error(exc)) from exc
        self._caps = None

    def disconnect(self) -> None:
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:  # noqa: BLE001 - closing must never raise at the UI
                pass
        self.transport = None
        self.session = None
        self._caps = None

    # -- plumbing ----------------------------------------------------------
    def _require(self):
        if self.session is None:
            raise ControllerError("Not connected.")
        return self.session

    def _call(self, fn, *args, **kwargs):
        """Run one core call, translating ``TLError`` into a friendly message."""
        try:
            return fn(*args, **kwargs)
        except t.TLError as exc:
            raise ControllerError(friendly_error(exc)) from exc

    # -- 1. capability view ------------------------------------------------
    def capabilities(self) -> dict:
        """Read the capability bitmap (0x8100): which commands this unit takes."""
        session = self._require()
        self._caps = self._call(session.negotiate_capabilities,
                                timeout=self.options.timeout)
        return self._caps

    def supports(self, cmd: int) -> bool:
        """Whether the last-read bitmap advertises ``cmd``."""
        return bool(self._caps and cmd in self._caps["commands"])

    # -- 2. device status --------------------------------------------------
    def status(self) -> dict:
        """Poll model, firmware, version, battery, wear, mode and use time."""
        return self._call(self._require().read_status, timeout=self.options.timeout)

    # -- 3. noise mode -----------------------------------------------------
    def noise_mode(self) -> int:
        return self._call(self._require().read_noise_mode, timeout=self.options.timeout)

    def set_noise_mode(self, value: int) -> dict:
        """Write the noise mode, then read it back (the push is racy).

        A write is accepted only while the buds are worn; otherwise the device
        answers ``0x8404 0e`` and :func:`friendly_error` explains it.
        """
        session = self._require()
        self._call(session.set_noise_mode, value, timeout=self.options.timeout)
        actual = self._call(session.read_noise_mode, timeout=self.options.timeout)
        return {"requested": value, "actual": actual, "ok": actual == value}
