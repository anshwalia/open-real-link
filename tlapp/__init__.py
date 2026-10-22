"""``tlapp`` -- the PySide6 desktop app over ``tlcore`` + ``tltransport``.

The app is split so that the interesting half needs no Qt:

* :mod:`tlapp.controller` is a **Qt-free** controller. It owns the connection and
  exposes one method per verified operation, so it can be driven against a
  recorded fixture with no display and no device.
* :mod:`tlapp.backupfile` is the Qt-free snapshot file I/O -- plain JSON, no
  database, atomic writes.
* :mod:`tlapp.window` is the widgets, and :mod:`tlapp.app` starts them.

Run it with ``python -m tlapp`` (see ``docs/app.md``). Importing ``tlapp`` stays
Qt-free; only ``tlapp.window`` imports PySide6.
"""
from __future__ import annotations

from .backupfile import (
    BackupFileError,
    default_backup_dir,
    read_snapshot,
    snapshot_summary,
    suggested_name,
    write_snapshot,
)
from .controller import (
    ConnectionOptions,
    ControllerError,
    DeviceController,
    EQ_PRESET_MAX,
    EQ_PRESET_MIN,
    eq_preset_choices,
    friendly_error,
    noise_choices,
)

__all__ = [
    "BackupFileError",
    "ConnectionOptions",
    "ControllerError",
    "DeviceController",
    "EQ_PRESET_MAX",
    "EQ_PRESET_MIN",
    "default_backup_dir",
    "eq_preset_choices",
    "friendly_error",
    "noise_choices",
    "read_snapshot",
    "snapshot_summary",
    "suggested_name",
    "write_snapshot",
]
