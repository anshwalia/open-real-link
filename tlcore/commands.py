"""TL command IDs, manager classes and the capability bitmap.

Pure data and pure functions: no I/O, no platform imports.  This is what keeps
``tlcore`` portable.  The tables were recovered by static analysis of the vendor
app; ``tests/parity_*`` cross-checks them against an external reference codec.

The capability bitmap is the real answer to "what does this device support" --
see :func:`parse_capabilities`.
"""
from __future__ import annotations

# --- command families: Protocol.a(cmd) = cmd & 0x7F00 -----------------------
CLASS_MASK = 0x7F00
RESPONSE_BIT = 0x8000

CLASS_POLL = 0x0100     # PollCommandManager
CLASS_NOTIFY = 0x0200   # NotificationCommandManager
CLASS_UPGRADE = 0x0300  # UpgradeManager
CLASS_SET = 0x0400      # SetCommandManager
CLASS_REQUEST = 0x0500  # RequestCommandManager  (device -> app)
CLASS_DEBUG = 0x0F00    # DebugCommandManager

CLASS_NAMES = {
    CLASS_POLL: "PollCommandManager",
    CLASS_NOTIFY: "NotificationCommandManager",
    CLASS_UPGRADE: "UpgradeManager",
    CLASS_SET: "SetCommandManager",
    CLASS_REQUEST: "RequestCommandManager",
    CLASS_DEBUG: "DebugCommandManager",
}

# --- command IDs recovered from decompiled manager bodies ------------------
CMD_NOTIFICATION_CAPS = 0x0200   # NotificationCommandManager.b
CMD_REGISTER_NOTIFICATION = 0x0202   # NotificationCommandManager.c
CMD_CANCEL_REGISTER_NOTIFICATION = 0x0203  # NotificationCommandManager.a
CMD_SET_KEY_FUNCTION = 0x0401    # SetCommandManager.s
CMD_SET_NOISE_REDUCTION = 0x0404  # SetCommandManager.a
CMD_EQ_INFO = 0x0418             # SetCommandManager.p (mode 5 == query)
CMD_GET_REMOTE_CAPABILITY = 0x0100  # PollCommandManager.C -> device reports capability bitmap
CMD_DETECT_FEATURE = 0x012A      # PollCommandManager.b  -> device reports one feature id + uint32 LE
CMD_GET_STATUS_FEATURE = 0x0129  # PollCommandManager.I
CMD_GET_NOISE_REDUCTION = 0x010C  # PollCommandManager.h/.y/z -> read a NoiseReductionInfo field

# 0x0404 action=1 payload[2] values (VERIFIED LIVE on Air7 Pro, buds WORN).
# These are the values the firmware both accepts on write and reports on
CAPABILITY_TABLE = [
    [0x0105], [0x0106], [0x0107],
    [0x0108, 0x0401], [0x0109], [0x0400],
    [0x0402], [0x010D, 0x0403], [0x010C, 0x0404],
    [0x0405], [0x0406, 0x010F], [0x0110, 0x0407],
    [], [0x0408], [0x0409],
    [0x040A, 0x0111], [], [],
    [], [0x040E, 0x040D, 0x0115, 0x0116], [],
    [], [0x0205], [0x0F00],
    [], [0x0118, 0x0411], [0x011A, 0x0412],
    [], [], [0x0112, 0x040B],
    [0x011E, 0x011F, 0x0415], [], [0x0120],
    [], [0x0122, 0x0418], [],
    [], [], [0x0124, 0x041B],
    [], [], [],
    [], [0x041E], [],
    [], [0x0128, 0x050E], [],
    [], [], [],
    [], [], [],
    [0x012C], [], [],
    [], [0x0131, 0x0428], [],
    [0x042C],
]
# Protocol.c2 -- always available, no capability check
ALWAYS_ON = {0x0100, 0x0101, 0x0102, 0x0103, 0x0104, 0x010B, 0x0F00}

# Human labels.  **Observed** names came from log strings in the decompiled
# manager bodies; family labels are structural.  An empty string means the
# command id is known to exist but its purpose was not recovered.
CMD_NAMES = {
    0x0100: "getRemoteCapability/getStatusFeature",
    0x0101: "getDeviceVersion/getProperty",
    0x0118: "getEarRestoreData",
    0x011E: "getEarScanData",
    0x011F: "getEarScanFilterData",
    0x0120: "getEarLanguageData",
    0x0122: "getAllEqInfo",
    0x0129: "getStatusFeature",
    0x012A: "detectFeature (capability report)",
    0x012C: "startSocketConnect",
    0x0200: "getNotificationCapabilities",
    0x0202: "registerNotification",
    0x0203: "cancelRegisterNotification",
    0x0205: "registerMultiNotification",
    0x0401: "setKeyFunction",
    0x0403: "setSwitchFeature",
    0x0404: "setNoiseReduction",
    0x0406: "setEQStatus",
    0x0407: "setHighVolume",
    0x040A: "setAutoPowerOffTime",
    0x040B: "setMultiConnectAction",
    0x040E: "switchProcessHearingDetectionParams",
    0x0411: "setRestoreData",
    0x0412: "setPersonalizedNoiseReduction",
    0x0415: "sendProcessEarScanData",
    0x0418: "setEqInfo/getEqInfo",
    0x041B: "setBassEngineValue",
    0x041E: "setSpatialAudioType",
    0x0428: "setAIStatus/setTranslationAppStatus",
    0x042C: "setHeadsetMicStatus",
    0x0F00: "switchDebugStatus",
}

assert len(CAPABILITY_TABLE) == 61, len(CAPABILITY_TABLE)


def parse_capabilities(payload: bytes) -> dict:
    """Decode the 0x8100 body (response to `PollCommandManager.C`, 0x0100):
    status byte + LSB-first capability bitmap.

    Returns {'status', 'bits', 'groups', 'commands', 'always_on'}
    where ``commands`` is the sorted set of command IDs the device accepts
    (union of the enabled groups and :data:`ALWAYS_ON`).
    """
    if len(payload) < 2:
        raise ValueError("capability payload needs a status byte plus >=1 bitmap byte")
    status = payload[0]
    bitmap = payload[1:]
    bit_count = min(len(bitmap) * 8, len(CAPABILITY_TABLE))
    bits, groups, cmds = [], [], set()
    for i in range(bit_count):
        if (bitmap[i >> 3] >> (i & 7)) & 1:
            bits.append(i)
            groups.append(CAPABILITY_TABLE[i])
            cmds.update(CAPABILITY_TABLE[i])
    cmds |= ALWAYS_ON
    return {
        "status": status,
        "ok": status == 0,
        "bits": bits,
        "groups": groups,
        "commands": sorted(cmds),
        "always_on": sorted(ALWAYS_ON),
    }


def cmd_class(cmd: int) -> int:
    """Protocol.a(): which manager owns this command id."""
    return cmd & CLASS_MASK


def is_response(cmd: int) -> bool:
    return bool(cmd & RESPONSE_BIT)


