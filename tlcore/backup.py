"""Back up and restore a device's user settings.

The TL protocol has **no single "read all settings" command**.  Like the vendor
app -- which negotiates the capability bitmap (``0x0100``) and then polls each
supported status once (``PollCommandManager.i``) -- a backup captures the raw
reply of a curated poll set, plus the capability bitmap and device identity.

Restore re-applies only the settings whose read and write forms are both
recovered *and* symmetric:

===============  ==========================  ==========================
setting          read                        write
===============  ==========================  ==========================
noise mode       ``0x010C`` sel ``1``        ``0x0404`` action ``1``
key function     ``0x0108``                  ``0x0401`` (same records)
EQ mode          ``0x010F``                  ``0x0406`` (identity)
high volume      ``0x0110``                  ``0x0407`` (boolean)
===============  ==========================  ==========================

Everything else is stored faithfully but reported as *not restorable* rather
than guessed at, because its payload value semantics were not recovered (see
``docs/command-coverage.md``).

Two settings used to be restorable and are not any more, both live-tested:
``switch_features`` (``0x0403``) because the Air7 Pro accepts the write and
changes nothing -- there is no observable effect to verify it against -- and
``bass_engine`` because there is no working *read* to capture a value with
(``0x0124`` is never answered).  Both stay captured where their read is
verified, and are reported ``captured, no restorer``.

This module is pure: it talks only to a
:class:`~tlcore.session.Session`, never to a file or a platform API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .commands import (
    CMD_SET_EQ_MODE,
    CMD_SET_HIGH_VOLUME,
    CMD_SET_KEY_FUNCTION,
    CMD_SET_NOISE_REDUCTION,
)
from .decoders import (
    parse_device_version,
    parse_eq_mode,
    parse_key_function,
    parse_model_id,
)
from .errors import TLError

#: Format tag written into (and required by) every backup file.
BACKUP_FORMAT = "open-real-link.backup/1"

#: Poll requests a backup captures, in the order the vendor app issues them
#: (a curated subset of ``PollCommandManager.i``): ``(cmd, payload, key)``.
READ_PLAN: tuple[tuple[int, bytes, str], ...] = (
    (0x0101, b"", "property"),
    (0x0103, b"", "model"),
    (0x0104, b"", "status_0104"),
    (0x0105, b"", "firmware"),
    (0x0107, b"", "status_0107"),
    (0x0108, b"", "key_function"),
    (0x010B, b"", "status_010b"),
    (0x010C, b"\x01", "noise_mode"),
    (0x010C, b"\x02", "noise_cycle"),
    (0x010D, b"", "switch_features"),
    (0x010F, b"", "eq_mode"),
    (0x0110, b"", "high_volume"),
    (0x0112, b"", "multi_point"),
    (0x0118, b"", "ear_restore"),
    (0x011A, b"", "personalized_anc"),
    (0x0122, b"", "eq_info"),
    (0x0131, b"", "ai_status"),
)

#: Raw capture keys that are user *settings* (as opposed to identity/status).
SETTING_KEYS = frozenset({
    "noise_mode", "noise_cycle", "key_function", "switch_features",
    "eq_info", "multi_point", "ear_restore",
    "personalized_anc", "ai_status", "status_0104", "status_0107",
    "status_010b", "high_volume",
})

#: Settings this module knows how to write back.
RESTORABLE = frozenset({"noise_mode", "key_function", "eq_mode", "high_volume"})


def _model_string(reply_hex: str) -> Optional[str]:
    model = parse_model_id(bytes.fromhex(reply_hex)).get("model_id")
    return f"0x{model:06X}" if model is not None else None


def _identity(raw: dict) -> dict:
    device: dict = {}
    if "model" in raw:
        device["model"] = _model_string(raw["model"]["reply"])
    if "firmware" in raw:
        device["firmware"] = parse_device_version(
            bytes.fromhex(raw["firmware"]["reply"]))["versions"]
    return device


def _derive_settings(raw: dict) -> dict:
    """Turn the raw capture into the minimal write-back values we trust."""
    out: dict = {}
    nm = raw.get("noise_mode")
    if nm and nm["status"] == 0 and len(nm["reply"]) >= 2:
        out["noise_mode"] = int(nm["reply"][-2:], 16)
    kf = raw.get("key_function")
    if kf and kf["status"] == 0:
        table = parse_key_function(bytes.fromhex(kf["reply"]))
        # [count][records...] is exactly the 0x0401 body; count 0 is not writable
        if table["count"]:
            out["key_function"] = f"{table['count']:02x}" + "".join(table["records"])
    em = raw.get("eq_mode")
    if em and em["status"] == 0:
        mode = parse_eq_mode(bytes.fromhex(em["reply"]))["mode"]
        if mode is not None:
            out["eq_mode"] = mode
    hv = raw.get("high_volume")
    if hv and hv["status"] == 0 and len(hv["reply"]) >= 2:
        out["high_volume"] = bool(int(hv["reply"][-2:], 16))
    return out


def capture(session, *, timeout: Optional[float] = None) -> dict:
    """Read the device's settings and return a serializable snapshot.

    Negotiates the capability bitmap first, then polls every supported entry in
    :data:`READ_PLAN`.  Requests the device does not support -- or that time
    out -- are recorded under ``skipped`` rather than aborting the backup.
    """
    caps = session.negotiate_capabilities(timeout=timeout)
    raw: dict = {}
    skipped: list = []
    for cmd, payload, key in READ_PLAN:
        if not session.supports(cmd):
            skipped.append({"key": key, "cmd": f"0x{cmd:04X}", "reason": "unsupported"})
            continue
        try:
            reply = session.request(cmd, payload, timeout=timeout)
        except TLError as exc:
            skipped.append({"key": key, "cmd": f"0x{cmd:04X}", "reason": str(exc)})
            continue
        raw[key] = {
            "cmd": f"0x{cmd:04X}",
            "request": payload.hex(),
            "reply": reply["payload"].hex(),
            "status": session.status_of(reply),
        }
    return {
        "format": BACKUP_FORMAT,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": _identity(raw),
        "capabilities": {"commands": caps["commands"], "bits": caps["bits"]},
        "settings": _derive_settings(raw),
        "raw": raw,
        "skipped": skipped,
    }


def restore(session, snapshot: dict, *, timeout: Optional[float] = None,
            dry_run: bool = False) -> dict:
    """Re-apply the restorable settings from ``snapshot``.

    Returns ``{"applied": [...], "not_restored": [...]}``.  Writes go through
    :class:`~tlcore.session.Session`, so the in-ear gate applies: a noise write
    while the buds are out raises :class:`~tlcore.errors.NotWorn`.
    """
    if not isinstance(snapshot, dict) or snapshot.get("format") != BACKUP_FORMAT:
        raise ValueError(f"not a {BACKUP_FORMAT} backup")

    if session.capabilities is None:
        session.negotiate_capabilities(timeout=timeout)

    settings = snapshot.get("settings") or {}
    applied: list = []
    not_restored: list = []

    def _apply(name: str, supported: bool, write) -> None:
        if not supported:
            not_restored.append({"setting": name, "reason": "unsupported by device"})
            return
        if dry_run:
            applied.append({"setting": name, "dry_run": True})
            return
        write()
        applied.append({"setting": name})

    noise_mode = settings.get("noise_mode")
    if noise_mode is not None:
        _apply("noise_mode", session.supports(CMD_SET_NOISE_REDUCTION),
               lambda: session.set_noise_mode(noise_mode, timeout=timeout))

    key_function = settings.get("key_function")
    if key_function:
        def _write_key_function() -> None:
            reply = session.request(CMD_SET_KEY_FUNCTION,
                                    bytes.fromhex(key_function), timeout=timeout)
            session.check_status(reply)
        _apply("key_function", session.supports(CMD_SET_KEY_FUNCTION), _write_key_function)

    eq_mode = settings.get("eq_mode")
    if eq_mode is not None:
        _apply("eq_mode", session.supports(CMD_SET_EQ_MODE),
               lambda: session.set_eq_mode(eq_mode, timeout=timeout))

    high_volume = settings.get("high_volume")
    if high_volume is not None:
        _apply("high_volume", session.supports(CMD_SET_HIGH_VOLUME),
               lambda: session.set_high_volume(high_volume, timeout=timeout))

    for key in (snapshot.get("raw") or {}):
        if key in SETTING_KEYS and key not in RESTORABLE:
            not_restored.append({"setting": key, "reason": "captured, no restorer"})

    return {"applied": applied, "not_restored": not_restored}
