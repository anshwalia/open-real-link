"""The host's Bluetooth radio: is it on, and can we turn it on?

OpenRealLink cannot reach a headset over a radio that is switched off, so the
app checks before it opens the main window (:mod:`tlapp.gate`). Reading the state
is the easy half. Switching it on is the interesting half:

* **macOS** -- there is **no public API** for it. CoreBluetooth can *observe* the
  controller state but not change it. The only programmatic switch is the private
  ``IOBluetoothPreferenceSetControllerPowerState`` in ``IOBluetooth.framework``
  (the one ``blueutil`` uses). It is verified to work on macOS 26.7.2, but it is
  undocumented and Apple could remove it, so :func:`set_bluetooth` only *fires*
  the request and the caller watches :func:`bluetooth_state` for the result.
  When it does not take, the UI falls back to **System Settings -> Bluetooth**
  (:func:`open_bluetooth_settings`).
* **Windows / Linux** -- not implemented: :func:`bluetooth_state` returns
  :data:`UNKNOWN`, and a caller that sees :data:`UNKNOWN` must **not** block, so
  a platform we cannot probe never stops a connection that would have worked.

This module is Qt-free on purpose, so it is testable with no display, and
importing ``tlapp`` still never imports PySide6.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

__all__ = [
    "OFF",
    "ON",
    "UNKNOWN",
    "bluetooth_state",
    "open_bluetooth_settings",
    "set_bluetooth",
    "wait_for_bluetooth",
]

#: The three answers :func:`bluetooth_state` can give.  ``UNKNOWN`` is a
#: deliberate "we cannot tell", not an error -- it means *do not block*.
ON = "on"
OFF = "off"
UNKNOWN = "unknown"

_MACOS_FRAMEWORK = "/System/Library/Frameworks/IOBluetooth.framework/IOBluetooth"

#: How often :func:`wait_for_bluetooth` looks, and its default budget.
_POLL_SECONDS = 0.2
DEFAULT_WAIT_SECONDS = 4.0

#: Cached ctypes handles, so ``CDLL`` runs once.  ``_PREFS_LOADED`` distinguishes
#: "not tried yet" from "tried and unavailable" (``None``).
_PREFS_LOADED = False
_PREFS = None


def _macos_prefs():
    """``(get, set)`` ctypes callables for the private macOS preference API.

    ``None`` off macOS, or if the private symbols are gone (in which case the
    caller reports :data:`UNKNOWN` and the app never blocks on the check).
    """
    global _PREFS_LOADED, _PREFS
    if _PREFS_LOADED:
        return _PREFS
    _PREFS_LOADED = True
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL(_MACOS_FRAMEWORK)
        get = lib.IOBluetoothPreferenceGetControllerPowerState
        get.restype = ctypes.c_int
        set_ = lib.IOBluetoothPreferenceSetControllerPowerState
        set_.argtypes = [ctypes.c_int]
        set_.restype = None
    except (OSError, AttributeError):
        return None
    _PREFS = (get, set_)
    return _PREFS


def bluetooth_state() -> str:
    """The radio's state as :data:`ON`, :data:`OFF` or :data:`UNKNOWN`.

    A caller that sees :data:`UNKNOWN` must continue as if Bluetooth were on --
    the check is a convenience, never a gate that could strand a working host.
    """
    prefs = _macos_prefs()
    if prefs is None:
        return UNKNOWN
    try:
        return ON if prefs[0]() else OFF
    except Exception:
        return UNKNOWN


def set_bluetooth(on: bool = True) -> bool:
    """Ask the OS to switch the radio.  **Asynchronous.**

    Returns whether the request was issued, not whether it took -- poll
    :func:`bluetooth_state` (or call :func:`wait_for_bluetooth`) for the result.
    ``False`` means there is no way to switch it on this platform, so the caller
    should fall back to :func:`open_bluetooth_settings`.
    """
    prefs = _macos_prefs()
    if prefs is None:
        return False
    try:
        prefs[1](1 if on else 0)
    except Exception:
        return False
    return True


def wait_for_bluetooth(state: str, timeout: float = DEFAULT_WAIT_SECONDS) -> bool:
    """Block until the radio reaches ``state``, or ``timeout`` passes."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if bluetooth_state() == state:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def open_bluetooth_settings() -> bool:
    """Open the OS's Bluetooth settings pane.  Return whether it was launched."""
    if sys.platform == "darwin":
        # Ventura+ pane id first, then the pre-Ventura one.
        return _run_first([
            ["open", "x-apple.systempreferences:com.apple.Bluetooth"],
            ["open", "x-apple.systempreferences:com.apple.preference.bluetooth"],
        ])
    if sys.platform == "win32":
        try:
            os.startfile("ms-settings:bluetooth")  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    return False


def _run_first(commands: list[list[str]]) -> bool:
    for command in commands:
        try:
            subprocess.run(command, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False
