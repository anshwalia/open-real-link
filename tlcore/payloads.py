"""Payload builders for the commands that were recovered in full.

Only the builders that are actually understood live here.  Everything else is
built by callers as raw bytes.
"""
from __future__ import annotations

import struct


def crc16_ccitt(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# =========================================================================
# Payload builders for commands recovered in full
# =========================================================================
def payload_set_noise_reduction(action: int, noise_type: int, value: int) -> bytes:
    """SetCommandManager.a() / cmd 0x0404:  payload {action, type, value}.

    action 1 -> 0x01, otherwise 0x02
    type   1 -> 0x01, otherwise 0x02
    value  raw byte (mode / cycle / ANC depth)

    Observed semantics on the Air7 Pro (docs/14 §7):

    * ``action=1`` sets the **active noise mode** (the field read back by
      ``0x010C 01 01``).  ``payload_set_noise_mode`` is the friendly wrapper.
      The firmware only accepts this while the buds are **worn (in-ear)**;
      when they are not, the device answers ``0x8404 0e``.
    * ``action=2`` sets the **noise cycle** (the ``0x010C 02 01`` bitmask).
    * ``action=3`` is accepted silently (``0x8404 00``) but no state change
      was observed; ``action`` 0/4/5/6/7 are rejected (``0x8404 01`` or
      ``0e``).
    """
    return bytes([0x01 if action == 1 else 0x02,
                  0x01 if noise_type == 1 else 0x02,
                  value & 0xFF])


def payload_set_noise_mode(value: int) -> bytes:
    """0x0404 action=1: set the active noise mode (requires buds worn).

    ``value`` is one of :data:`NOISE_MODE_NORMAL` (0x01),
    :data:`NOISE_MODE_TRANSPARENCY` (0x02), :data:`NOISE_MODE_ANC` (0x20).
    """
    return payload_set_noise_reduction(1, 1, value)


def payload_set_key_function(entries: list[bytes]) -> bytes:
    """SetCommandManager.s() / cmd 0x0401: [count][ (4-byte entry) x count ]."""
    return bytes([len(entries) & 0xFF]) + b"".join(e[:4].ljust(4, b"\x00") for e in entries)


def payload_set_switch_feature(feature: int, enabled: bool | int) -> bytes:
    """SetCommandManager.y() / cmd 0x0403: ``[featureId][enabled ? 1 : 0]``.

    The read half is ``0x010D`` (:func:`tlcore.decoders.parse_switch_features`).
    """
    return bytes([feature & 0xFF, 1 if enabled else 0])


def payload_set_high_volume(enabled: bool | int) -> bytes:
    """SetCommandManager.r() / cmd 0x0407: ``[enabled ? 1 : 0]``.

    **Verified live** on the Air7 Pro (2026-10-20): ``0x0407 00`` moves the
    ``0x0110`` read-back from ``00 01`` to ``00 00``, and ``0x0407 01`` restores
    it.  The write is accepted only while the buds are worn (else ``0x8407 0e``);
    the device also pushes ``0x0204 0f 08 00 <v>``.  The read half is ``0x0110``
    (:func:`tlcore.decoders.parse_high_volume`).
    """
    return bytes([1 if enabled else 0])


def payload_set_find_mode(on: bool | int) -> bytes:
    """SetCommandManager.q() / cmd 0x0400: ``[on ? 1 : 0]``.

    **Verified live** on the Air7 Pro (2026-10-20): ``0x0400 01`` and
    ``0x0400 00`` both answer ``0x8400 00``.  There is no read pair -- the
    device reports no find-mode state, only the ack.

    The app never sends this command (no caller anywhere in its code), so it is
    the one write this project drives purely from static recovery plus a live
    test.  **It rings the buds loudly**: always follow an ``on`` with an
    ``off``.  :meth:`tlcore.session.Session.set_find_mode` enforces that by
    sending the stop itself.
    """
    return bytes([1 if on else 0])


def payload_compactness_detect(action: bool | int) -> bytes:
    """SetCommandManager.z() / cmd 0x0405: ``[action ? 1 : 0]``.

    ``TLHeadsetConnectionImp.c2`` maps the ``DetectAction`` to ``0``/``1``.
    **Verified live** on the Air7 Pro (2026-10-20): ``0x0405 01`` is accepted
    (``0x8405 00``) and the firmware pushes ``0x0204 04 <2 records>``; ``00``
    stops it.  Sensor only -- it drives no audio.  The result event is decoded
    by :func:`tlcore.decoders.parse_compactness_event`.
    """
    return bytes([1 if action else 0])


def payload_set_ai_status(state: int, ai_type: int) -> bytes:
    """SetCommandManager.j() / cmd 0x0428: ``[state][type]``.

    ``state`` is ``AIStatus`` (0 = off, 1 = on); ``ai_type`` is ``AIType``
    (``CommandProtocol`` values 0, 1, 2, 4).  **Verified live** on the Air7 Pro
    (2026-10-20): ``state=1`` activates the AI/translation session for ``type``
    and the device echoes it on both the ``0x0204 0f 02 00 <state> <type>`` push
    and the ``0x0131`` read (:func:`tlcore.decoders.parse_ai_status`).
    ``state=0`` deactivates **only with the matching active type**; ``type=4``
    is accepted but not applied by FW ``1.1.0.85``.  The state is session-scoped
    (it resets when the link closes).
    """
    return bytes([state & 0xFF, ai_type & 0xFF])


def payload_set_eq(mode: int, min_value: int, max_value: int, eq_id: int,
                   name: str = "", points: list[tuple[int, int]] | None = None) -> bytes:
    """SetCommandManager.p() / cmd 0x0418.

    [mode][minValue][maxValue][eqId][nameLen][name utf8]
    [freqCount][ (freq uint16 LITTLE endian)(db int8) x freqCount ]

    The frequency is written with `ProtocolUtil.c(..., true)` (little-endian).
    mode == 5 means "query current EQ" and carries no further payload.
    """
    if mode == 5:
        return bytes([5])
    name_bytes = name.encode("utf-8")
    out = bytearray([mode & 0xFF, min_value & 0xFF, max_value & 0xFF, eq_id & 0xFF,
                     len(name_bytes)])
    out += name_bytes
    points = points or []
    out.append(len(points) & 0xFF)
    for freq, db in points:
        out += struct.pack("<H", freq & 0xFFFF)
        out.append(db & 0xFF)
    return bytes(out)


def payload_set_eq_mode(mode: int) -> bytes:
    """SetCommandManager.n() / cmd 0x0406: ``[mode]``.

    Selects the active sound-effect/EQ preset.  **Verified live** on the
    Air7 Pro: ``0x010F`` reads the value back identity over the range 0..6,
    and values >= 7 clamp to 6.
    """
    return bytes([mode & 0xFF])


def payload_set_bass_engine(min_value: int, max_value: int, current_value: int,
                            type: int | None = None) -> bytes:
    """SetCommandManager.l()/m() / cmd 0x041B.

    ``[minValue][maxValue][currentValue]`` -- 3 bytes; a ``type`` byte is
    appended only when ``type > 0`` (``SetCommandManager.m`` passes ``-1`` for
    the plain 3-byte form).  The matching read is ``0x0124``
    (:func:`tlcore.decoders.parse_bass_engine`).
    """
    body = bytes([min_value & 0xFF, max_value & 0xFF, current_value & 0xFF])
    if type is not None and type > 0:
        body += bytes([type & 0xFF])
    return body
