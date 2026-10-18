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
