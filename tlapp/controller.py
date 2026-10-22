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

This file implements the app feature by feature -- connect + capability view,
device status, noise mode get/set, the equalizer / high-volume settings,
settings backup / restore, the push-event monitor, the gesture table, and the
guarded raw console. Later features (voice) are added here as they are built, so
the app never carries a control for something that is not yet wired up.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import tlcore as t
from tlcli.cli import noise_name, open_session

from . import backupfile

#: The **verified** preset range.  ``0x010F``/``0x0406`` accept `0`..`6` and read
#: back identity; writing `7` or `8` clamps to `6` (Air7 Pro, 2026-10-20).  The
#: app offers only the distinct presets, so it never writes a value the device
#: would silently change.
EQ_PRESET_MIN = 0
EQ_PRESET_MAX = 6

#: How long one event-monitor poll may block, in seconds.  The monitor runs on
#: the GUI thread, so this is also the worst-case input latency while it is on.
EVENT_POLL_SECONDS = 0.05

#: Default seconds the raw console listens for a reply after sending.
SEND_LISTEN_SECONDS = 1.0

__all__ = [
    "ConnectionOptions",
    "ControllerError",
    "DeviceController",
    "EQ_PRESET_MAX",
    "EQ_PRESET_MIN",
    "EVENT_POLL_SECONDS",
    "SEND_LISTEN_SECONDS",
    "eq_preset_choices",
    "event_phrase",
    "friendly_error",
    "function_choices",
    "noise_choices",
    "parse_command",
    "parse_payload",
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


def parse_command(text: str) -> int:
    """Parse a command id the way ``tlcli send`` does.

    ``int(text, 0)`` means ``0x0404`` is hex but a bare ``404`` is **decimal** --
    the same rule in both front ends, so an id pasted from the docs behaves
    identically wherever it is typed.
    """
    text = (text or "").strip()
    if not text:
        raise ControllerError("Enter a command id, e.g. 0x0404.")
    try:
        value = int(text, 0)
    except ValueError:
        raise ControllerError(
            f"A command id must be numeric, e.g. 0x0404 (got {text!r}).") from None
    if not 0 <= value <= 0xFFFF:
        raise ControllerError(f"Command id out of range: {text!r}")
    return value


def parse_payload(text: str) -> bytes:
    """Parse a hex payload, tolerating spaces, commas and ``0x`` prefixes.

    Empty text is an empty payload: plenty of commands take none.
    """
    text = (text or "").strip()
    if not text:
        return b""
    cleaned = re.sub(r"\s+", "", text.replace("0x", "").replace("0X", ""))
    cleaned = cleaned.replace(",", "")
    if len(cleaned) % 2:
        raise ControllerError(
            f"A payload is whole bytes, so its hex needs an even number of digits "
            f"(got {len(cleaned)} in {text!r}).")
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        raise ControllerError(
            f"A payload must be hex, e.g. '01 01 20' (got {text!r}).") from None


def function_choices() -> list[tuple[int, str]]:
    """The selectable key functions, in wire-value order.

    The labels are the app's own enum (recovered from ``KeyTextUtils.a``), and
    the read side proves the encoding: a unit reports exactly these bytes in its
    ``0x0108`` table.  A value the unit will not *take* is not filtered out
    here -- the write is read back and compared, so the device has the last
    word instead of a shipped guess.
    """
    return sorted(t.FUNCTION_NAMES.items())


def event_phrase(row: dict) -> str:
    """One line describing a decoded push, for the event monitor.

    Takes a :func:`tlcore.decode_event` row.  Every branch is guarded by the
    key it needs, because a monitor must render whatever the device sends --
    including a packet no decoder understood.
    """
    if "press" in row:
        press = row["press"]
        return (f"button press: {press['function_name']} "
                f"(dev={press['device_type']} btn={press['button']} "
                f"action={press['button_action']} scene={press['scene']})")
    if "compactness" in row:
        return "compactness: " + ", ".join(
            f"{side['side_name']}={side['value']}" for side in row["compactness"])
    if "multi_connect" in row:
        entries = row["multi_connect"]["entries"]
        names = ", ".join(entry["name"] or entry["mac"] for entry in entries)
        return f"multiConnect: {row['multi_connect']['count']} device(s) {names}"
    if "public_event" in row:
        public = row["public_event"]
        parts = []
        if public["volume"] is not None:
            parts.append(f"volume={public['volume']}")
        if public["ai"] is not None:
            parts.append(f"ai={public['ai']['state']}/{public['ai']['type']}")
        return f"publicEvent flags=0x{public['flags']:04X} " + " ".join(parts)
    if "request" in row:
        return f"request: {row['request']}"
    if row.get("tlv"):
        pairs = ", ".join(f"{key}={value}" for key, value in row["tlv"].items())
        return f"{row.get('event', '')} {{{pairs}}}"
    if row.get("body"):
        return f"body {row['body']}"
    if "state" in row:
        return str(row["state"])
    return row.get("payload", "")


def eq_preset_choices() -> list[tuple[int, str]]:
    """The selectable sound-effect presets, in wire-value order.

    No preset *names* were recovered: the device reports the active preset as a
    plain number and the ``0x0122`` list carries whatever names it chooses, so
    the combo stays neutral and the table shows the device's own labels.
    """
    return [(value, f"Preset {value}")
            for value in range(EQ_PRESET_MIN, EQ_PRESET_MAX + 1)]


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

    # -- 4. equalizer ------------------------------------------------------
    def eq_mode(self) -> int:
        """Read the active sound-effect preset (``0x010F``)."""
        return self._call(self._require().read_eq_mode, timeout=self.options.timeout)

    def eq_presets(self) -> dict:
        """Read the preset / custom-EQ list (``0x0122``)."""
        return self._call(self._require().read_eq_info, timeout=self.options.timeout)

    def set_eq_mode(self, mode: int) -> dict:
        """Write the preset, then read it back (the ``0x0504`` push is racy).

        The write is read back identity for `0`..`6`; the caller should not pass
        anything else, or the device clamps it and the check reports a mismatch.
        """
        session = self._require()
        self._call(session.set_eq_mode, mode, timeout=self.options.timeout)
        actual = self._call(session.read_eq_mode, timeout=self.options.timeout)
        return {"requested": mode, "actual": actual, "ok": actual == mode}

    # -- 5. high volume ----------------------------------------------------
    def high_volume(self) -> bool:
        """Read the loudness-boost flag (``0x0110``)."""
        return self._call(self._require().read_high_volume, timeout=self.options.timeout)

    def set_high_volume(self, enabled: bool) -> dict:
        """Write the loudness boost, then read it back.

        Accepted only while the buds are worn, like the noise-mode write.
        """
        session = self._require()
        self._call(session.set_high_volume, enabled, timeout=self.options.timeout)
        actual = self._call(session.read_high_volume, timeout=self.options.timeout)
        return {"requested": bool(enabled), "actual": bool(actual),
                "ok": bool(actual) == bool(enabled)}

    # -- find my buds ------------------------------------------------------
    def find_buds(self, on: bool) -> dict:
        """Ring the buds (``0x0400 01``) or stop ringing (``0x0400 00``).

        **There is no read pair** for this command, so there is nothing to read
        back -- the ack is all the device gives.  What comes back is therefore
        what was *requested*, not a confirmed state; the caller owns the
        "ringing" flag.  ``on=True`` rings loudly.
        """
        status = self._call(self._require().set_find_mode, bool(on),
                            timeout=self.options.timeout)
        return {"requested": bool(on), "status": status, "ok": status == 0}

    # -- paired devices ----------------------------------------------------
    def paired_devices(self) -> dict:
        """Read the multipoint / paired-device list (``0x0112``).

        The names in it are the **host's own Bluetooth names** for the machines
        this headset has been paired with -- real, user-identifying strings.
        Showing them to the user is the point; persisting them is not, which is
        why the backup file flags ``multi_point`` as containing paired-device
        names and why this is never written into a promoted fixture.
        """
        return self._call(self._require().read_multi_connect,
                          timeout=self.options.timeout)

    # -- bass engine -------------------------------------------------------
    def bass_engine(self) -> dict:
        """Read the multi-band bass engine (``0x012C``).

        Read-only on purpose.  The ``0x041B`` write is accepted by the device,
        but *which* band its ``type`` byte selects is not verified, so a write
        control would be guessing at the effect.  ``0x0124``, the id this was
        long assumed to be, is never answered by an Air7 Pro.
        """
        return self._call(self._require().read_multi_bass_engine,
                          timeout=self.options.timeout)

    # -- expert diagnostics ------------------------------------------------
    def identity(self) -> dict:
        """Read the identity block: vendor id, protocol version, colour id.

        None of these mean anything to a listener -- they identify the *wire*
        peer, not the product -- which is exactly why they live behind the
        Expert tab rather than on Device.
        """
        session = self._require()
        timeout = self.options.timeout
        return {
            "vendor_id": self._call(session.read_remote_vendor_id, timeout=timeout),
            "protocol_version": self._call(session.read_protocol_version,
                                          timeout=timeout),
            "color_id": self._call(session.read_color_id, timeout=timeout),
        }

    def upgrade_capability(self) -> dict:
        """Read the OTA capability list (``0x0107``)."""
        return self._call(self._require().read_upgrade_capability,
                          timeout=self.options.timeout)

    def switch_features(self) -> dict:
        """Read the switch / feature status list (``0x010D``)."""
        return self._call(self._require().read_switch_features,
                          timeout=self.options.timeout)

    def notification_capabilities(self) -> dict:
        """Read the push-event types the device supports (``0x0200``)."""
        return self._call(self._require().read_notification_capabilities,
                          timeout=self.options.timeout)

    def personalized_anc(self) -> dict:
        """Read the personalized-noise result-exists flag (``0x011A``)."""
        return self._call(self._require().read_personalized_anc,
                          timeout=self.options.timeout)

    def ai_status(self) -> dict:
        """Read the AI / translation status (``0x0131``)."""
        return self._call(self._require().read_ai_status,
                          timeout=self.options.timeout)

    # -- 6. backup / restore -----------------------------------------------
    def backup(self) -> dict:
        """Capture the device's settings into a snapshot dict (no file written)."""
        snapshot = self._call(t.capture, self._require(), timeout=self.options.timeout)
        # capture negotiates the bitmap itself; keep the gating view in step
        self._caps = snapshot["capabilities"]
        return snapshot

    def backup_to(self, path) -> tuple[pathlib.Path, dict]:
        """Capture and write a snapshot; returns ``(written_path, summary)``."""
        snapshot = self.backup()
        written = self._file(backupfile.write_snapshot, path, snapshot)
        return written, backupfile.snapshot_summary(snapshot)

    def preview_restore(self, path) -> dict:
        """Report what a restore of ``path`` would do, **without writing**.

        Uses the core's own dry run, so the preview and the real restore cannot
        disagree about which settings are restorable.
        """
        snapshot = self._file(backupfile.read_snapshot, path)
        return self._call(t.restore, self._require(), snapshot,
                          timeout=self.options.timeout, dry_run=True)

    def restore_from(self, path) -> dict:
        """Re-apply the restorable settings from the snapshot at ``path``."""
        snapshot = self._file(backupfile.read_snapshot, path)
        return self._call(t.restore, self._require(), snapshot,
                          timeout=self.options.timeout)

    # -- 7. gesture / key-function table -----------------------------------
    def gestures(self) -> dict:
        """Read the key-function/gesture table (``0x0108``)."""
        return self._call(self._require().read_key_function,
                          timeout=self.options.timeout)

    def set_gestures(self, updates: dict) -> dict:
        """Patch the gesture table with ``{(device, button, action): function}``.

        ``0x0401`` replaces the **whole** table, so this reads it, patches only
        the named rows, writes every row back, and reads it again.  Refuses to
        write when the device returned no table, or when a named gesture is not
        in it -- a partial edit must never silently drop the other rows.

        Returns ``{"changed", "status", "verified", "table"}``; ``verified`` is
        whether the read-back matches what was written.
        """
        entries = self._gesture_entries()
        present = {(e.device_type, e.button, e.action) for e in entries}
        missing = [key for key in updates if key not in present]
        if missing:
            raise ControllerError("no such gesture in the device's table: " + ", ".join(
                f"{device}:{button}:{action}" for device, button, action in missing))
        return self._write_gestures(entries, t.patch_key_functions(entries, updates))

    def disable_long_press(self) -> dict:
        """Turn every long-press gesture off (``function`` ``0``).

        Same shape as :meth:`set_gestures`.  **Verified live**: the Air7 Pro
        accepted the whole-table write, read the change back, and the original
        table restored cleanly.
        """
        entries = self._gesture_entries()
        return self._write_gestures(entries, t.disable_long_press(entries))

    def _gesture_entries(self) -> list:
        table = self.gestures()
        if not table["entries"]:
            raise ControllerError(
                "the device returned an empty gesture table; refusing to write it back")
        return table["entries"]

    def _write_gestures(self, before: list, entries: list) -> dict:
        session = self._require()
        changed = [{"device_type": old.device_type, "button": old.button,
                    "action": old.action, "from": old.function, "to": new.function}
                   for old, new in zip(before, entries) if old != new]
        if not changed:
            return {"changed": [], "status": None, "verified": None,
                    "table": {"status": 0, "count": len(before), "entries": before}}
        status = self._call(session.set_key_function, entries,
                            timeout=self.options.timeout)
        after = self._call(session.read_key_function, timeout=self.options.timeout)
        return {"changed": changed, "status": status,
                "verified": list(after["entries"]) == list(entries), "table": after}

    # -- 9. guarded raw console --------------------------------------------
    def send_raw(self, cmd: int, payload: bytes = b"", *, type_byte: int = 0,
                 listen: float = SEND_LISTEN_SECONDS) -> dict:
        """Send one raw command; return the frames sent and everything heard.

        Refuses the tone / OTA / debug families **outright**.  There is
        deliberately no override here: ``tlcli send --force`` is the documented
        escape hatch for someone who has read the protocol notes, and a
        one-click override in a window is precisely the accident the guard
        exists to prevent.
        """
        family = t.dangerous_family(cmd)
        if family is not None:
            raise ControllerError(
                f"0x{cmd:04X} is part of the {family}, which is deliberately not "
                "implemented. The app will not send it; use `tlcli send --force` "
                "if you have read the protocol notes and know why you want it.")
        session = self._require()
        frames = self._call(session.send, cmd, payload, type_byte)
        packets = self._call(session.listen, listen) if listen > 0 else []
        return {"cmd": cmd, "payload": payload, "type_byte": type_byte,
                "listen": listen, "frames": frames, "packets": packets,
                "events": [t.decode_event(packet) for packet in packets]}

    # -- 8. push-event monitor ---------------------------------------------
    def poll_events(self, seconds: float = EVENT_POLL_SECONDS) -> list[dict]:
        """Read device pushes for up to ``seconds`` and decode them.

        Bounded, but blocking -- the window calls this from a timer with a
        short budget, because the transport has one reader and the controller
        is deliberately single-threaded.  Pushes that a request parked while it
        waited for its reply are drained here too, so nothing is lost and
        nothing is shown twice.
        """
        session = self._require()
        parked = list(session.events)
        session.events.clear()
        packets = parked + self._call(session.listen, seconds)
        return [t.decode_event(packet) for packet in packets]

    def event_types(self) -> dict:
        """Read the push-event types this unit sends (``0x0200``)."""
        return self._call(self._require().read_notification_capabilities,
                          timeout=self.options.timeout)

    @staticmethod
    def _file(fn, *args):
        """Run a :mod:`tlapp.backupfile` call, phrasing its failure for a person."""
        try:
            return fn(*args)
        except backupfile.BackupFileError as exc:
            raise ControllerError(str(exc)) from exc


