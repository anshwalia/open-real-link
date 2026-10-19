"""Single-instance guard for the app.

Two ``tlapp`` processes must not run at once. The device has **one** RFCOMM
slot: a second instance would fight the first for channel 12 and produce
``0xE00002BC`` (channel refused) on whichever loses the race -- a confusing
failure with nothing in the UI to explain it. A second launch should instead
raise the window that is already open.

The guard is a ``QLockFile`` under the user's cache directory, so it is released
automatically if a process dies (``QLockFile`` records the PID and treats a
stale lock from a dead process as free). It is deliberately Qt-only and lives in
this module rather than the controller, which stays Qt-free.
"""
from __future__ import annotations

import os
import pathlib

from PySide6.QtCore import QLockFile, QStandardPaths

__all__ = ["InstanceLock", "default_lock_path"]

#: Override the lock path. Used by the tests so they never contend with a real
#: running instance, and useful for running two builds side by side.
LOCK_PATH_ENV = "OPEN_REAL_LINK_LOCK"


def default_lock_path() -> pathlib.Path:
    """Where the single-instance lock lives.

    Uses ``GenericCacheLocation``, **not** ``CacheLocation``: the latter is
    scoped to the application name, so it resolves to ``.../Caches/Python`` when
    the app is launched as ``python -m tlapp`` but ``.../Caches/open-real-link``
    from a packaged build -- two different lock files, and the guard would
    silently stop guarding. ``GenericCacheLocation`` is ``~/Library/Caches`` on
    macOS, the XDG cache dir on Linux and ``%LOCALAPPDATA%`` on Windows, and
    does not depend on the app name.
    """
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericCacheLocation)
    if not base:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation)
    override = os.environ.get(LOCK_PATH_ENV)
    if override:
        path = pathlib.Path(override)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    root = pathlib.Path(base or pathlib.Path.home() / ".cache") / "open-real-link"
    root.mkdir(parents=True, exist_ok=True)
    return root / "open-real-link.lock"


class InstanceLock:
    """Holds the single-instance lock; released on :meth:`release` or GC.

    Use as a context manager::

        with InstanceLock() as lock:
            if not lock.acquired:
                raise SystemExit(lock.explain())
            ...
    """

    def __init__(self, path: pathlib.Path | None = None):
        self.path = pathlib.Path(path) if path is not None else default_lock_path()
        self._lock = QLockFile(str(self.path))
        # do not steal a lock from a live process; a stale one is reclaimed
        self._lock.setStaleLockTime(30_000)
        self.acquired = self._lock.tryLock(100)

    def explain(self) -> str:
        """A one-line message naming the process already holding the lock."""
        pid, host, app = self._lock.getLockInfo()
        if pid:
            return (f"OpenRealLink is already running (pid {pid}"
                    + (f" on {host}" if host else "") + "). "
                    "Use the window that is already open; only one instance can "
                    "hold the headset's RFCOMM channel.")
        return "OpenRealLink is already running. Only one instance can hold the headset."

    def release(self) -> None:
        if self.acquired:
            self._lock.unlock()
            self.acquired = False

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.release()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass
