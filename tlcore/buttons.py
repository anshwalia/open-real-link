"""Buttons, gestures and the key-function table.

A headset stores a small table of **key functions**: each entry binds one
physical gesture on one earbud to one function.  It is read with an empty
``0x0108`` and written with ``0x0401``; both carry the same 4-byte record,
``KeyFunctionInfo``::

    [deviceType][button][action][function]

A live press is *not* a ``0x8108`` frame: the device pushes it as a ``0x0204``
notification whose first byte is the event type ``0xF1`` (``userInteraction``).
:func:`parse_user_interaction` decodes that body.

Pure data and pure functions -- no I/O, no platform imports.

The field maps below were recovered from ``ParamsConverter.r``/``.A`` (the tuple
and the wire function byte), ``wm.SettingsConverter.b``/``.d`` (the gesture and
function enums) and ``KeyTextUtils.a``/``.b`` (the human labels).  Values the
app renders as a string resource are labelled with the resource name.
"""
from __future__ import annotations

from typing import NamedTuple

#: ``KeyFunctionInfo.deviceType`` (``ParamsConverter.r``).
DEVICE_TYPE_NAMES = {
    1: "left",
    2: "right",
    4: "both",
}

#: ``KeyFunctionInfo.action`` -- the gesture.  2/3/4 are pinned by
#: ``SettingsConverter.b`` (double=2, triple=3, long-press=4/5).
ACTION_NAMES = {
    1: "single-click",
    2: "double-click",
    3: "triple-click",
    4: "long-press",
    5: "long-press-alt",
    6: "extra-gesture",
}

#: ``KeyFunctionInfo.function`` -- the wire byte.  ``KeyTextUtils.a`` labels the
#: app's ``settingValue`` and ``ParamsConverter.A`` maps it to this byte.
FUNCTION_NAMES = {
    0: "off",
    1: "play-pause",
    3: "voice-assistant-alt",
    4: "voice-assistant",
    5: "previous-track",
    6: "next-track",
    8: "noise-control",
    11: "volume-up",
    12: "volume-down",
    13: "device-change",
    17: "game-mode",
    26: "face-to-face-translation",
    27: "ai-home-income-2",
    31: "ai-assist",
}

#: ``0x0204`` notification event type for a button press.
EVENT_USER_INTERACTION = 0xF1


class KeyFunctionInfo(NamedTuple):
    """One ``[deviceType][button][action][function]`` record."""

    device_type: int
    button: int
    action: int
    function: int

    def to_bytes(self) -> bytes:
        """Serialise to the 4 bytes ``0x0401`` writes."""
        return bytes([self.device_type & 0xFF, self.button & 0xFF,
                      self.action & 0xFF, self.function & 0xFF])

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> KeyFunctionInfo:
        return cls(data[offset], data[offset + 1], data[offset + 2], data[offset + 3])

    def describe(self) -> str:
        """Human-readable summary, e.g. ``"right double-click -> play-pause"``."""
        return (f"{DEVICE_TYPE_NAMES.get(self.device_type, self.device_type)} "
                f"{ACTION_NAMES.get(self.action, self.action)} -> "
                f"{FUNCTION_NAMES.get(self.function, self.function)}")


def parse_key_function_table(payload: bytes) -> dict:
    """Decode a ``0x8108`` body: ``[status][count] + count x 4``.

    Returns ``{"status", "count", "entries", "records"}`` where ``entries`` is a
    list of :class:`KeyFunctionInfo` and ``records`` the same records as hex (the
    shape :func:`tlcore.decoders.parse_key_function` returns).
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "count": 0,
                "entries": [], "records": []}
    status, count = payload[0], payload[1]
    entries, records = [], []
    for i in range(count):
        off = 2 + 4 * i
        if off + 4 > len(payload):
            break
        entries.append(KeyFunctionInfo.from_bytes(payload, off))
        records.append(payload[off:off + 4].hex())
    return {"status": status, "count": count, "entries": entries, "records": records}


def parse_user_interaction(payload: bytes) -> dict:
    """Decode the body of a ``0x0204`` event type ``0xF1`` (a button press).

    ``CommandUtil.p`` -> ``UserInteractionEventInfo(int, byte[])``::

        [deviceType:1][button:1][buttonAction:1][function:1][scene:1]
        options: (len-5)//2 x uint16 LITTLE endian

    ``payload`` is the notification body **after** the event-type byte, i.e.
    ``parse_notification_event(pkt)["body"]``.  Returns ``{}`` when shorter than
    the 5-byte minimum.
    """
    if len(payload) < 5:
        return {}
    function = payload[3]
    out = {
        "device_type": payload[0],
        "button": payload[1],
        "button_action": payload[2],
        "function": function,
        "function_name": FUNCTION_NAMES.get(function, f"0x{function:02X}"),
        "scene": payload[4],
        "options": [],
    }
    rest = payload[5:]
    for i in range(len(rest) // 2):
        out["options"].append(int.from_bytes(rest[2 * i:2 * i + 2], "little"))
    return out
