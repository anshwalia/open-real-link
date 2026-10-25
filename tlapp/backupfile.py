"""Read and write backup snapshots as **plain JSON files**.

A backup is one small document -- identity, the capability bitmap, the raw reply
of each polled setting, and the derived write-back values -- and that is exactly
the dict :func:`tlcore.capture` returns and :func:`tlcore.restore` consumes.  The
file is therefore ``json.dumps(snapshot, indent=2)`` and nothing else, which
means a file written here restores from ``tlcli restore`` and a file written by
``tlcli backup`` restores here.

There is deliberately **no database**.  A snapshot is a document, not a dataset:
nothing queries or joins it, nothing updates it in place, and it is a few
kilobytes.  Keeping it a plain file keeps it inspectable, diffable and
redactable -- which matters, because a snapshot of a unit that supports
``0x0112`` embeds the host's **paired-device names** and is personal data.

Writes are atomic (temp file + :func:`os.replace`), so a crash or a full disk
can never leave a half-written backup behind.

Qt-free and device-free: this module is importable and testable without PySide6
and without a headset.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

import tlcore as t

__all__ = [
    "BackupFileError",
    "default_backup_dir",
    "read_snapshot",
    "snapshot_summary",
    "suggested_name",
    "write_snapshot",
]


class BackupFileError(Exception):
    """A backup file could not be read, or a snapshot could not be written."""


def default_backup_dir() -> pathlib.Path:
    """The per-user directory backups default to, created on demand.

    ``~/Library/Application Support/open-real-link/backups`` on macOS, the XDG
    data directory on Linux, ``%APPDATA%`` on Windows.
    """
    if sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home())
    else:
        base = pathlib.Path(os.environ.get("XDG_DATA_HOME")
                            or pathlib.Path.home() / ".local" / "share")
    return base / "open-real-link" / "backups"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def suggested_name(snapshot: dict) -> str:
    """A filename that says which unit and when, e.g. ``buds-air7-pro-....json``.

    Falls back to the raw model id, then to ``device``, when the model is not in
    the known-device table -- a backup of an unknown unit must still be saved.
    """
    _require_snapshot(snapshot)
    model = (snapshot.get("device") or {}).get("model") or ""
    name = ""
    if isinstance(model, str) and model.startswith("0x") and len(model) == 8:
        spec = t.device_spec(model[2:])
        name = spec.name if spec else ""
    stem = _slug(name) or _slug(model) or "device"

    stamp = ""
    created = snapshot.get("created")
    if isinstance(created, str) and created:
        try:
            when = datetime.fromisoformat(created.replace("Z", "+00:00"))
            stamp = when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except ValueError:
            stamp = _slug(created)
    return f"{stem}-{stamp}.json" if stamp else f"{stem}.json"


def _require_snapshot(snapshot) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("format") != t.BACKUP_FORMAT:
        raise BackupFileError(f"not a {t.BACKUP_FORMAT} snapshot")
    return snapshot


def write_snapshot(path, snapshot: dict) -> pathlib.Path:
    """Write ``snapshot`` to ``path`` atomically, creating the directory.

    Returns the resolved path so the caller can echo it.  Raises
    :class:`BackupFileError` for a bad snapshot or an unwritable location.
    """
    _require_snapshot(snapshot)
    path = pathlib.Path(path).expanduser()
    try:
        if path.parent and not path.parent.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupFileError(
            f"cannot create {path.parent}: {exc.strerror or exc}") from exc

    text = json.dumps(snapshot, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # atomic: a reader never sees a partial file
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise BackupFileError(
            f"cannot write {path.name}: {exc.strerror or exc}") from exc
    return path


def read_snapshot(path) -> dict:
    """Read and validate a snapshot file.

    Refuses anything that is not a ``tlcore.BACKUP_FORMAT`` document -- a JSON
    file that merely *looks* like a backup must not be replayed onto a device.
    """
    path = pathlib.Path(path).expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BackupFileError(
            f"cannot read {path.name}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BackupFileError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != t.BACKUP_FORMAT:
        found = data.get("format") if isinstance(data, dict) else type(data).__name__
        raise BackupFileError(
            f"{path.name} is not a {t.BACKUP_FORMAT} snapshot (found {found!r}). "
            "Restore accepts only a file this app or `tlcli backup` wrote.")
    return data


def snapshot_summary(snapshot: dict) -> dict:
    """What a file contains, for display -- no device involved.

    ``restorable`` are the settings a restore can re-apply; ``captured_only``
    were read faithfully but have no recovered writer, and are reported rather
    than guessed at.
    """
    _require_snapshot(snapshot)
    raw = snapshot.get("raw") or {}
    settings = snapshot.get("settings") or {}
    device = snapshot.get("device") or {}
    return {
        "created": snapshot.get("created"),
        "model": device.get("model"),
        "firmware": list(device.get("firmware") or []),
        "captured": len(raw),
        "skipped": len(snapshot.get("skipped") or []),
        "restorable": sorted(k for k in t.backup.RESTORABLE if k in settings),
        "captured_only": sorted(k for k in raw if k in t.backup.SETTING_KEYS
                                and k not in t.backup.RESTORABLE),
        "contains_paired_devices": "multi_point" in raw,
    }
