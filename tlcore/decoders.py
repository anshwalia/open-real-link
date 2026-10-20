"""Decoders for the payloads we understand.

Device state arrives two ways: as a reply to a poll (``0x81xx``) and as an
unsolicited push (``0x0204``).  Both carry the same TLV shapes, so the parsers
here are shared between them.

TLV convention used by most of these bodies::

    [status-or-event][count][id, value] x count       # 1-byte values
    [status-or-event][count][id, uint32 LE] x count  # use-time

with ids ``1/2/3 = left/right/case``.
"""
from __future__ import annotations

import re
import struct


def parse_feature_detect(payload: bytes) -> dict:
    """Decode the 0x812A body from `PollCommandManager.b` / `O`.

    Layout (all read via `ProtocolUtil`, so multi-byte fields are LE):

        payload[0]    status code (0 == success)
        payload[1]    featureId   (echo of the requested type)
        payload[2:6]  value       uint32 LITTLE endian

    `O` builds `FeatureDetectBean(payload[1], ProtocolUtil.e(payload,2,4,true))`.
    """
    if len(payload) < 6:
        raise ValueError("0x812A body needs status(1) + featureId(1) + value(4)")
    status = payload[0]
    return {
        "status": status,
        "ok": status == 0,
        "feature_id": payload[1],
        "value": struct.unpack_from("<I", payload, 2)[0],
    }
# NotificationCommandManager.k(): device -> app event pushed in a 0x0204
# packet.  payload[0] is the event type; the rest is event-specific TLVs.
# Names corrected 2026-10-20 from the decompiled dispatch (the earlier labels
# 0x04-0x08/0x0B/0x0F were wrong):
#   0x04 compactness        CommandUtil.e -> CompactnessDetectionInfoSdk
#   0x05 gameMode           -> SettingsInfo(settingType 3)  -> I5
#   0x06 multiConnect       CommandUtil.h -> MultiConnectInformations (paired list)
#   0x07 partyMode          -> SettingsInfo(settingType 12) -> Q5
#   0x08 hearingProtecting  "HEARING_PROTECTING_STATUS_CHANGED"
#   0x0B personalizedNoiseReduction  CommandUtil.m -> PersonalizedNoiseReductionResult
#   0x0F publicEvent        NotificationCommandManager.e ("handlePublicEvent";
#                           flag word: &8 = volume level, &2 = AI state)
EVENT_TYPE_NAMES = {
    0x01: "battery",
    0x02: "earbudStatus",
    0x03: "noiseReduction",
    0x04: "compactness",
    0x05: "gameMode",
    0x06: "multiConnect",
    0x07: "partyMode",
    0x08: "hearingProtecting",
    0x0B: "personalizedNoiseReduction",
    0x0E: "earScanResult",
    0x0F: "publicEvent",
    0xF1: "userInteraction",
}


def parse_notification_event(payload: bytes) -> dict:
    """Decode a device->app 0x0204 notification event.

    `NotificationCommandManager.t` routes cmd 0x0204 to `l` -> `k`, which
    switches on the *first* payload byte:

        0x01 battery            CommandUtil.d
        0x02 earbud status      CommandUtil.f
        0x03 noise-reduction    NoiseReductionInfo
        0x04 compactness / 0x05 game mode / 0x06 multi-connect / 0x07 party mode
        0x08 hearing-protect / 0x0B personalized noise / 0x0E ear-scan
        0x0F public event / 0xF1 UI  (see `EVENT_TYPE_NAMES`)

    Only the type byte and raw body are exposed here; the per-type TLV
    decoders live in CommandUtil and are out of scope for the capture tool.
    """
    if not payload:
        return {"event": None, "event_name": "empty", "body": b"", "tlv": {}}
    event = payload[0]
    body = payload[1:]
    out = {
        "event": event,
        "event_name": EVENT_TYPE_NAMES.get(event, f"0x{event:02X}"),
        "body": body,
        "tlv": {},
    }
    # Best-effort TLV.  Most 0x0204 bodies are [count][id, value]*count with
    # single-byte ids/values, e.g. battery `03 01 64 02 64 03 00` ->
    # {1:100, 2:100, 3:0} (left=100, right=100, case=0).
    if body and (len(body) - 1) == 2 * body[0]:
        out["tlv"] = {body[1 + 2 * i]: body[2 + 2 * i] for i in range(body[0])}
    if event == 0x04:
        out["compactness"] = parse_compactness_event(body)
    elif event == 0x0F:
        out["public_event"] = parse_public_event(body)
    return out


COMPACTNESS_SIDES = {0x01: "left", 0x02: "right"}


def parse_compactness_event(body: bytes) -> list[dict]:
    """``CommandUtil.e()``: the ``0x0204`` event-``0x04`` body is two records.

    Each record is ``[earSide][value]``; ``CompactnessDetectionInfoSdk`` reads
    ``body[i]`` and ``body[i + 1]``.  **Verified live** on the Air7 Pro
    (2026-10-20): ``0x0405 01`` pushed ``04 01 04 02 04`` and, on a second run,
    ``04 01 00 02 00`` -- so ``value`` is a live status code, not a constant.
    """
    if len(body) < 4:
        return []
    return [
        {"side": body[i],
         "side_name": COMPACTNESS_SIDES.get(body[i], f"0x{body[i]:02X}"),
         "value": body[i + 1]}
        for i in (0, 2)
    ]


def parse_public_event(body: bytes) -> dict:
    """``NotificationCommandManager.e()``: the ``0x0204`` event-``0x0F`` body.

    A 2-byte little-endian **flag word**, then the value for the set flag:
    bit 3 (``& 8``) = volume level (one byte), bit 1 (``& 2``) = AI state
    (``[state][type]``).  **Verified live** on the Air7 Pro (2026-10-20): the
    high-volume write pushes ``0f 08 00 <v>`` and the AI write pushes
    ``0f 02 00 <state> <type>``.
    """
    if len(body) < 2:
        return {"flags": None, "volume": None, "ai": None}
    flags = body[0] | (body[1] << 8)
    out: dict = {"flags": flags, "volume": None, "ai": None}
    if flags & 0x8 and len(body) >= 3:
        out["volume"] = body[2]
    if flags & 0x2 and len(body) >= 4:
        out["ai"] = {"state": body[2], "type": body[3]}
    return out


# --------------------------------------------------------------------------
# Device state  (0x0106 battery, 0x0109 wear, 0x0128 use time, ...)
# --------------------------------------------------------------------------
#: TLV ids: 1/2/3 = left / right / case
DEVICE_NAMES = {1: "L", 2: "R", 3: "CASE"}
CASE_ID = 3

#: Wear state reported by ``0x0109``.  **Only 3 is confirmed** -- it is the
#: value the device reports exactly when noise-mode writes are accepted, which
#: is the in-ear gate.  The meaning of 0/1/2/4 was never established, so they
#: are deliberately not named here; print the raw number instead of guessing.
WEAR_IN_EAR = 3

#: ``0x8105`` is a per-chip component-version table; the realme Air7 Pro reports
#: ``1,1,111,1,2,1.1.0.85,...`` so the interesting field is the dotted version.
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")


def parse_pairs(payload: bytes) -> tuple[int, list[tuple[int, int]]]:
    """``[status][count][id, value] x count`` -> ``(status, [(id, value)])``."""
    if len(payload) < 2:
        return (payload[0] if payload else 0), []
    status, count = payload[0], payload[1]
    out: list[tuple[int, int]] = []
    for i in range(count):
        off = 2 + i * 2
        if off + 1 >= len(payload):
            break
        out.append((payload[off], payload[off + 1]))
    return status, out


def parse_battery(payload: bytes) -> dict:
    """``0x8106`` and ``0x0204 event 01``.

    Value byte: bit 7 = charging, bits 0-6 = level percent.  Real Air7 Pro
    reply: ``00 03 01 64 02 64 03 00`` -> left 100%, right 100%, case 0%.

    A case level of 0 is what the device reports when the buds are **out** of
    the case; it does not mean an empty battery (docs/16).
    """
    status, pairs = parse_pairs(payload)
    return {
        "status": status,
        "devices": {
            DEVICE_NAMES.get(i, str(i)): {"level": v & 0x7F, "charging": bool(v & 0x80)}
            for i, v in pairs
        },
    }


def parse_earbud_status(payload: bytes) -> dict:
    """``0x8109`` -- per-device status byte; see :data:`WEAR_IN_EAR`."""
    status, pairs = parse_pairs(payload)
    return {
        "status": status,
        "devices": {DEVICE_NAMES.get(i, str(i)): v for i, v in pairs},
        "in_ear": pairs and all(v == WEAR_IN_EAR for i, v in pairs if i != CASE_ID),
    }


def parse_use_time(payload: bytes) -> dict:
    """``0x8128``: ``[status][count][id, uint32-LE seconds] x count``."""
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "devices": {}}
    status, count = payload[0], payload[1]
    out: dict[str, int] = {}
    for i in range(count):
        off = 2 + i * 5
        if off + 4 >= len(payload):
            break
        out[DEVICE_NAMES.get(payload[off], str(payload[off]))] = int.from_bytes(
            payload[off + 1:off + 5], "little")
    return {"status": status, "devices": out}


def parse_model_id(payload: bytes) -> dict:
    """``0x8103``: ``[status] + 3-byte little-endian model id`` (``0x064C12``)."""
    if len(payload) < 4:
        return {"status": payload[0] if payload else None, "model_id": None}
    return {"status": payload[0], "model_id": int.from_bytes(payload[1:4], "little")}


def parse_device_version(payload: bytes) -> dict:
    """``0x8105``: ASCII component-version table -> the dotted versions in it."""
    text = payload[1:].decode("ascii", "ignore")
    seen, out = set(), []
    for m in _VERSION_RE.finditer(text):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(m.group(0))
    return {"status": payload[0] if payload else None, "versions": out}


def parse_eq_info(payload: bytes) -> dict:
    """``0x8122`` (``getAllEqInfo``): ``[status][count] + records``.

    Record layout recovered from ``CommandUtil.b`` (``parseAllEqData``)::

        [isSelected][minValue][maxValue][eqId][nameLen][name utf8]
        [freqCount][ (freq uint16 LE)(db int8) x freqCount ]

    The frequency is read through ``ProtocolUtil.e(..., true)`` -- the same
    little-endian helper the ``0x0418`` writer uses (docs/12).  The Air7 Pro
    reports a single point-less entry: ``00 01 01 01 02 04 00 00`` -> eqId 4,
    selected, ``min=1``, ``max=2``, no bands.
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "count": 0, "entries": []}
    status, count = payload[0], payload[1]
    entries: list[dict] = []
    off = 2
    for _ in range(count):
        if off + 5 > len(payload):
            break
        is_selected, min_value, max_value, eq_id, name_len = payload[off:off + 5]
        off += 5
        if off + name_len + 1 > len(payload):
            break
        name = payload[off:off + name_len].decode("utf-8", "replace")
        off += name_len
        freq_count = payload[off]
        off += 1
        if off + 3 * freq_count > len(payload):
            break
        points = []
        for _band in range(freq_count):
            points.append({"freq": int.from_bytes(payload[off:off + 2], "little"),
                           "db": int.from_bytes(payload[off + 2:off + 3], "little", signed=True)})
            off += 3
        entries.append({"is_selected": is_selected, "min": min_value, "max": max_value,
                        "eq_id": eq_id, "name": name, "points": points})
    return {"status": status, "count": count, "entries": entries}


def parse_eq_mode(payload: bytes) -> dict:
    """``0x810F`` (``getEQStatus``): ``[status][mode]``.

    The mode is the active sound-effect/EQ preset.  **Verified live** on the
    Air7 Pro: the ``0x0406`` write value is read back here identity over the
    range 0..6, and >=7 clamps to 6 (see ``docs/protocol.md``).
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "mode": None}
    return {"status": payload[0], "mode": payload[1]}


def parse_high_volume(payload: bytes) -> dict:
    """``0x8110`` (``getHighVolume``): ``[status][0|1]``.

    **Verified live** on the Air7 Pro (2026-10-20): pairs with the ``0x0407``
    write (:func:`tlcore.payloads.payload_set_high_volume`); the read-back
    follows the write and the restore returns it.  ``enabled`` is ``None`` when
    the reply is too short.
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "enabled": None}
    return {"status": payload[0], "enabled": bool(payload[1])}


def parse_ai_status(payload: bytes) -> dict:
    """``0x8131`` (``getAIStatus``): ``[status][state][type]``.

    Pairs with the ``0x0428`` write (:func:`tlcore.payloads.payload_set_ai_status`).
    **Verified live** on the Air7 Pro (2026-10-20): after ``0x0428 01 01`` the
    reply is ``00 01 01``; after ``01 02`` it is ``00 01 02``.  ``state`` and
    ``type`` are ``None`` when the reply is too short.
    """
    if len(payload) < 3:
        return {"status": payload[0] if payload else 0, "state": None, "type": None}
    return {"status": payload[0], "state": payload[1], "type": payload[2]}


def parse_personalized_anc(payload: bytes) -> dict:
    """``0x811A`` (``getPersonalizedNoiseReductionResult``): ``[status][exists]``.

    ``PollCommandManager.A`` sends the empty ``0x011A`` request; ``j0`` builds
    ``PersonalizedNoiseReductionResultExist(1, payload)``, whose constructor
    reads ``payload[1]``.  **Verified live** on the Air7 Pro (2026-10-20): the
    reply is ``00 00`` -- status 0, no stored result.  ``exists`` is the flag
    for a stored personalized-noise calibration result; the trigger is the
    ``0x0412`` calibration (not a toggle), so this read is not a boolean
    read-back of any ``0x0412`` write.
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "exists": None}
    return {"status": payload[0], "exists": payload[1]}


def parse_notification_capabilities(payload: bytes) -> dict:
    """``0x8200`` (``getNotificationCapabilities``): ``[status][count][type...]``.

    ``NotificationCommandManager.i`` reads the status, then a count, then that
    many event-type bytes.  **Verified live** on the Air7 Pro (2026-10-20): the
    reply is ``00 02 02 01`` -> status 0, count 2, types ``[2, 1]``.  These are
    the ``0x0204`` event types the device can push.
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "count": 0, "types": []}
    status, count = payload[0], payload[1]
    return {"status": status, "count": count, "types": list(payload[2:2 + count])}


def parse_multi_connect(payload: bytes) -> dict:
    """``0x8112`` (``getMultiConnect``): the paired-device / multipoint list.

    Layout (``CommandUtil.h``): ``[status][count]`` then ``count`` records of::

        [mac:6][length:1][state:1][flag:1][nameLen:1][name:nameLen]

    ``length == nameLen + 3`` is the record stride minus 7; ``name`` is
    NUL-terminated (``nameLen`` includes the NUL).  The MAC is stored
    **least-significant byte first**; ``ProtocolUtil.b(..., true)`` reverses it,
    so ``mac`` here is in the app's display order.  **Verified live** on the
    Air7 Pro (2026-10-20): 167 bytes, 7 entries, all bytes consumed.

    ``state`` and ``flag`` are byte fields whose meaning is **inferred**: on the
    observed unit the connected/primary entry was ``state=2, flag=1`` and the
    rest ``0, 0``.
    """
    status = payload[0] if payload else 0
    count = payload[1] if len(payload) > 1 else 0
    entries = []
    off = 2
    for _ in range(count):
        if off + 10 > len(payload):
            break
        mac = payload[off:off + 6]
        length = payload[off + 6]
        state = payload[off + 7]
        flag = payload[off + 8]
        name_len = payload[off + 9]
        name = payload[off + 10:off + 10 + name_len]
        entries.append({
            "mac": ":".join(f"{b:02x}" for b in reversed(mac)),
            "name": name.rstrip(b"\x00").decode("utf-8", "replace"),
            "state": state,
            "flag": flag,
        })
        off += 7 + length
    return {"status": status, "count": count, "entries": entries}


def parse_bass_engine(payload: bytes) -> dict:
    """``0x8124``: ``[status][min][max][current]([type])`` -- the bass engine.

    Pairs with ``0x041B setBassEngineValue``
    (:func:`tlcore.payloads.payload_set_bass_engine`).  The layout is recovered
    from ``PollCommandManager.Q``
    (which reads the status, then ``CommandUtil.c(1, body)``); it is **inferred**,
    not live-verified -- the Air7 Pro advertises the capability bit but does not
    answer ``0x0124``.  ``value`` is ``None`` when the reply carries no reading.
    """
    status = payload[0] if payload else 0
    value = None
    if status == 0 and len(payload) >= 4:
        value = {
            "min": payload[1],
            "max": payload[2],
            "current": payload[3],
            "type": payload[4] if len(payload) >= 5 else None,
        }
    return {"status": status, "value": value}


def parse_key_function(payload: bytes) -> dict:
    """``0x8108``: ``[status][count] + 4 bytes per entry`` -- the gesture table.

    The same 4-byte records are what ``0x0401 setKeyFunction`` writes, so the
    body **minus the status byte** is a valid ``0x0401`` payload and can be
    replayed to restore the mapping (see :mod:`tlcore.backup`).
    """
    if len(payload) < 2:
        return {"status": payload[0] if payload else 0, "count": 0, "records": []}
    count = payload[1]
    records = [payload[2 + 4 * i:6 + 4 * i].hex() for i in range(count)
               if 6 + 4 * i <= len(payload)]
    return {"status": payload[0], "count": count, "records": records}


def parse_switch_features(payload: bytes) -> dict:
    """``0x810D``: ``[status][count] + (feature, enabled) x count``.

    Pairs with the write ``0x0403 setSwitchFeature`` (``[feature][enabled]``).
    The Air7 Pro reports ``count 0`` -- it advertises the capability but exposes
    no switch features (see ``docs/live-test.md``).
    """
    status, pairs = parse_pairs(payload)
    return {
        "status": status,
        "entries": [{"feature": f, "enabled": v} for f, v in pairs],
    }


#: command id -> decoder, for the state bodies we actually understand.
#: Used by the CLI to decode a poll reply or a push without a switch statement.
STATE_DECODERS = {
    0x8106: parse_battery,
    0x8109: parse_earbud_status,
    0x8128: parse_use_time,
    0x8103: parse_model_id,
    0x8105: parse_device_version,
    0x8122: parse_eq_info,
    0x8124: parse_bass_engine,
    0x810F: parse_eq_mode,
    0x8110: parse_high_volume,
    0x8112: parse_multi_connect,
    0x811A: parse_personalized_anc,
    0x8200: parse_notification_capabilities,
    0x8131: parse_ai_status,
    0x812A: parse_feature_detect,
    0x8108: parse_key_function,
    0x810D: parse_switch_features,
}


def decode_state(cmd: int, payload: bytes) -> dict | None:
    """Decode ``cmd``'s payload with the matching decoder, or ``None``."""
    fn = STATE_DECODERS.get(cmd)
    return fn(payload) if fn else None
