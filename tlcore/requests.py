"""Decoders for the device -> app request family (``0x05xx``).

Unlike the ``0x81xx`` replies, these are *unsolicited requests the device makes
of the host*.  ``RequestCommandManager.f`` dispatches on the command id:

===========  ==========================  ====================================
command      handler                     meaning
===========  ==========================  ====================================
``0x0500``   ``i``                       asks the host for the time; the host
                                         answers ``0x8500 [00][unix:4 LE]``
``0x0502``   ``h``                       asks the app to run an action
``0x0503``   ``a``                       feature-switch status sync
``0x0504``   ``e``                       the device changed EQ mode
``0x0506``   ``d``                       custom-EQ list
``0x0508``   ``c``                       bass-engine status
``0x050E``   ``g``                       per-device use time
``0x0516``   ``b``                       framed audio data from the device
===========  ==========================  ====================================

Only ``0x0500`` has a host reply.  Everything is little-endian where it is
multi-byte (``ProtocolUtil.e``/``.c`` with ``true``).  Pure functions, no I/O.
"""
from __future__ import annotations

import struct

#: ``0x0502`` action -> the ``RequestCommandManager`` handler message.
OPERATION_MESSAGES = {5: 54, 6: 55}

#: ``0x050E`` / use-time device ids, same as the state decoders.
DEVICE_NAMES = {1: "L", 2: "R", 3: "CASE"}


def payload_host_time_reply(unix_seconds: int) -> bytes:
    """Build the host reply to ``0x0500`` (``0x8500``): ``[00][unix:4 LE]``.

    ``RequestCommandManager.i`` sends ``System.currentTimeMillis() / 1000``.
    """
    return b"\x00" + struct.pack("<I", unix_seconds & 0xFFFFFFFF)


def parse_host_time_request(payload: bytes) -> dict:
    """The device -> app ``0x0500`` body.

    The app ignores the body and replies with the current time, so the layout is
    not authoritative.  Expose the raw bytes, and a uint32 LE when the body is
    exactly 4 bytes (the observed case), clearly marked as unconfirmed.
    """
    out: dict = {"raw": payload.hex()}
    if len(payload) == 4:
        out["value_le"] = struct.unpack_from("<I", payload, 0)[0]
    return out


def parse_operation_request(payload: bytes) -> dict:
    """``0x0502``: ``[action:1]``.

    ``action`` 5 -> handler message 54, 6 -> 55; any other value is ignored by
    the app.  The user-facing meaning of 5/6 was not recovered.
    """
    if not payload:
        return {"action": None, "message": None}
    action = payload[0]
    return {"action": action, "message": OPERATION_MESSAGES.get(action)}


def parse_feature_switch_status(payload: bytes) -> dict:
    """``0x0503``: ``[count:1] + count x [featureType:1][status:1]``.

    ``CommandUtil.i`` -> ``FeatureSwitchInfo``.  Each pair is forwarded to
    ``PollCommandManager.w0``, which maps ``featureType`` to a cached-feature
    handler (the same handlers the poll commands feed).
    """
    if not payload:
        return {"count": 0, "entries": []}
    count = payload[0]
    entries = []
    for i in range(count):
        off = 1 + 2 * i
        if off + 2 > len(payload):
            break
        entries.append({"feature_type": payload[off], "status": payload[off + 1]})
    return {"count": count, "entries": entries}


def parse_eq_mode_request(payload: bytes) -> dict:
    """``0x0504``: ``[eqMode:1]`` -- the device changed the sound-effect/EQ mode."""
    if not payload:
        return {"eq_mode": None}
    return {"eq_mode": payload[0]}


def parse_custom_eq(payload: bytes) -> dict:
    """``0x0506``: the custom-EQ list (``CommandUtil.b``).

    ``[count:1] + count x {isSelected:1, minValue:1, maxValue:1, eqId:1,
    nameLen:1, name:nameLen utf8, freqNum:1, freqNum x {freq:2 LE, db:1}}``.
    """
    if not payload:
        return {"count": 0, "entries": []}
    count = payload[0]
    entries = []
    off = 1
    for _ in range(count):
        if off + 5 > len(payload):
            break
        is_selected, min_value, max_value, eq_id, name_len = payload[off:off + 5]
        off += 5
        if off + name_len + 1 > len(payload):
            break
        name = payload[off:off + name_len].decode("utf-8", "replace")
        off += name_len
        freq_num = payload[off]
        off += 1
        if off + 3 * freq_num > len(payload):
            break
        points = []
        for _band in range(freq_num):
            points.append({"freq": struct.unpack_from("<H", payload, off)[0],
                           "db": struct.unpack_from("<b", payload, off + 2)[0]})
            off += 3
        entries.append({"is_selected": is_selected, "min": min_value, "max": max_value,
                        "eq_id": eq_id, "name": name, "points": points})
    return {"count": count, "entries": entries}


def parse_bass_engine_status(payload: bytes) -> dict:
    """``0x0508``: ``[min:int8][max:int8][current:int8]`` + optional ``[type:int8]``.

    ``CommandUtil.c`` -> ``BassEngineInfo``; the bean stores **signed** values
    (``setMinValue(bArr[i])`` promotes the Java ``byte``), so a ``0xFB`` is
    ``-5``, not ``251``.  **Verified live** on the Air7 Pro (2026-10-20): the
    write ``0x041B [min][max][current]`` makes the device push ``0x0508``, and
    a ``-5..+5`` range comes back as ``fb 05`` (``min=-5, max=5``); the device
    keeps its own fixed range and only echoes ``current``
    (``0x041B 00 0a fd`` -> ``0x0508 fb 05 fd 00``, ``current=-3``).
    """
    if len(payload) < 3:
        return {"min": None, "max": None, "current": None, "type": None}
    min_v, max_v, cur = struct.unpack_from("<bbb", payload, 0)
    out = {"min": min_v, "max": max_v, "current": cur, "type": None}
    if len(payload) >= 4:
        out["type"] = struct.unpack_from("<b", payload, 3)[0]
    return out


def parse_use_time_list(payload: bytes) -> dict:
    """``0x050E``: ``[count:1] + count x [deviceType:1][seconds:4 LE]``.

    ``CommandUtil.o`` -> ``UseTimeInfo`` (stride 5).
    """
    if not payload:
        return {"count": 0, "entries": []}
    count = payload[0]
    entries = []
    for i in range(count):
        off = 1 + 5 * i
        if off + 5 > len(payload):
            break
        device = payload[off]
        entries.append({"device_type": device,
                        "device": DEVICE_NAMES.get(device, str(device)),
                        "seconds": struct.unpack_from("<I", payload, off + 1)[0]})
    return {"count": count, "entries": entries}


def parse_oneshot_audio(payload: bytes) -> dict:
    """``0x0516``: framed audio data from the device (``AudioDataInfo.initData``).

    ``[frameNum:2 LE][channelCode:2 LE] + frameNum x
    {seqNum:4 LE, dataSize:2 LE, data:dataSize}``.
    ``channelCode`` 1 -> 1 channel, anything else -> 2 (per ``initData``).
    """
    if len(payload) < 4:
        return {"frame_num": 0, "channel": None, "frames": []}
    frame_num = struct.unpack_from("<H", payload, 0)[0]
    channel = 1 if struct.unpack_from("<H", payload, 2)[0] == 1 else 2
    frames = []
    off = 4
    for _ in range(frame_num):
        if off + 6 > len(payload):
            break
        seq_num = struct.unpack_from("<I", payload, off)[0]
        data_size = struct.unpack_from("<H", payload, off + 4)[0]
        off += 6
        if data_size <= 0 or off + data_size > len(payload):
            break
        frames.append({"seq_num": seq_num, "data": payload[off:off + data_size]})
        off += data_size
    return {"frame_num": frame_num, "channel": channel, "frames": frames}


#: command id -> decoder for the device -> app request family.
REQUEST_DECODERS = {
    0x0500: parse_host_time_request,
    0x0502: parse_operation_request,
    0x0503: parse_feature_switch_status,
    0x0504: parse_eq_mode_request,
    0x0506: parse_custom_eq,
    0x0508: parse_bass_engine_status,
    0x050E: parse_use_time_list,
    0x0516: parse_oneshot_audio,
}


def decode_request(cmd: int, payload: bytes) -> dict | None:
    """Decode ``cmd``'s device -> app body with the matching decoder, or ``None``."""
    fn = REQUEST_DECODERS.get(cmd)
    return fn(payload) if fn else None
