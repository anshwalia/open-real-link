"""TL command IDs, manager classes and the capability bitmap.

Pure data and pure functions: no I/O, no platform imports.  This is what keeps
``tlcore`` portable.  The tables were recovered by static analysis of the vendor
app; ``tests/parity_*`` cross-checks them against an external reference codec.

The capability bitmap is the real answer to "what does this device support" --
see :func:`parse_capabilities`.
"""
from __future__ import annotations

from typing import NamedTuple

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
CMD_SET_SWITCH_FEATURE = 0x0403  # SetCommandManager.y -> setSwitchFeature
CMD_SET_NOISE_REDUCTION = 0x0404  # SetCommandManager.a
CMD_EQ_INFO = 0x0418             # SetCommandManager.p (mode 5 == query)
CMD_GET_EQ_MODE = 0x010F         # PollCommandManager.j -> read the sound-effect/EQ mode
CMD_GET_ALL_EQ_INFO = 0x0122     # PollCommandManager.d -> read the EQ preset/custom list
CMD_SET_EQ_MODE = 0x0406         # SetCommandManager.n/.o -> write the sound-effect/EQ mode
CMD_GET_REMOTE_CAPABILITY = 0x0100  # PollCommandManager.C -> device reports capability bitmap
CMD_DETECT_FEATURE = 0x012A      # PollCommandManager.b  -> device reports one feature id + uint32 LE
CMD_GET_STATUS_FEATURE = 0x0129  # PollCommandManager.I
CMD_GET_NOISE_REDUCTION = 0x010C  # PollCommandManager.h/.y/z -> read a NoiseReductionInfo field
CMD_GET_KEY_FUNCTION = 0x0108    # PollCommandManager.h0 -> the gesture/key-function table
CMD_GET_MULTI_CONNECT = 0x0112   # PollCommandManager -> the paired-device / multipoint list
CMD_GET_BASS_ENGINE = 0x0124     # PollCommandManager.Q -> single bass-engine {min,max,current(,type)}
CMD_SET_BASS_ENGINE = 0x041B     # SetCommandManager.l/.m -> set the bass-engine value
CMD_SET_HIGH_VOLUME = 0x0407     # SetCommandManager.r -> setHighVolume (bool)
CMD_GET_HIGH_VOLUME = 0x0110     # PollCommandManager.s -> high-volume status
CMD_SET_AI_STATUS = 0x0428       # SetCommandManager.j -> setAIStatus / setTranslationAppStatus
CMD_GET_AI_STATUS = 0x0131       # PollCommandManager -> AI / translation status
CMD_COMPACTNESS_DETECT = 0x0405  # SetCommandManager.z -> in-ear detect trigger (action byte)
CMD_NOTIFICATION_EVENT = 0x0204  # NotificationCommandManager.k -> device -> app event push
CMD_REQUEST_HOST_TIME = 0x0500   # RequestCommandManager.i -> device asks the host for the time
CMD_REQUEST_OPERATION = 0x0502   # RequestCommandManager.h -> device asks the app to run an action
CMD_FEATURE_SWITCH_STATUS = 0x0503  # RequestCommandManager.a -> feature-switch sync
CMD_EQUALIZER_MODE = 0x0504      # RequestCommandManager.e -> the device changed EQ mode
CMD_CUSTOM_EQ = 0x0506           # RequestCommandManager.d -> custom-EQ list
CMD_BASS_ENGINE_STATUS = 0x0508  # RequestCommandManager.c -> bass-engine status
CMD_USE_TIME = 0x050E            # RequestCommandManager.g -> per-device use time
CMD_ONESHOT_AUDIO_DATA = 0x0516  # RequestCommandManager.b -> framed audio from the device

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
    0x0108: "getKeyFunction",
    0x0118: "getEarRestoreData",
    0x011E: "getEarScanData",
    0x011F: "getEarScanFilterData",
    0x0120: "getEarLanguageData",
    0x0122: "getAllEqInfo",
    0x0129: "getStatusFeature",
    0x012A: "detectFeature",
    0x012C: "startSocketConnect",
    0x0200: "getNotificationCapabilities",
    0x0202: "registerNotification",
    0x0203: "cancelRegisterNotification",
    0x0204: "notificationEvent",
    0x0205: "registerMultiNotification",
    0x0401: "setKeyFunction",
    0x0403: "setSwitchFeature",
    0x0404: "setNoiseReduction",
    0x0400: "setFindMode",
    0x0405: "setCompactnessDetect",
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
    0x0500: "requestHostTime",
    0x0502: "requestOperation",
    0x0503: "featureSwitchStatus",
    0x0504: "equalizerMode",
    0x0506: "customEq",
    0x0508: "bassEngineStatus",
    0x050E: "useTime",
    0x0516: "oneshotAudioData",
    0x0F00: "switchDebugStatus",
}

assert len(CAPABILITY_TABLE) == 61, len(CAPABILITY_TABLE)


class CommandSpec(NamedTuple):
    """One request command in the full recovered catalogue.

    ``family`` is the manager family ("poll", "notify", "OTA", "set",
    "device->app", "debug"); ``gating`` is "always-on" (in ``Protocol.c2``),
    "ungated" (sent with no capability check), or "cap-bit N" (enabled when the
    device sets capability bit N).  ``name`` is the observed name where one was
    recovered, else "".
    """

    cmd: int
    family: str
    gating: str
    name: str


#: The **complete** recovered command catalogue: all 79 request IDs the TL
#: protocol defines, whether or not ``tlcore`` implements them.
#: ``CAPABILITY_TABLE`` + ``ALWAYS_ON`` cover only the gated subset (48 IDs);
#: this is the full list, so a caller can identify any ID it sees on the wire.
#: ``docs/command-coverage.md`` is the prose view of the same table.
#: (``0x0F00`` is both always-on and in capability group 23; always-on wins.)
#:
#: The parity harness compares this against the reference codec, and
#: ``tests/test_command_registry.py`` checks it against ``CAPABILITY_TABLE``,
#: ``ALWAYS_ON`` and ``CMD_NAMES``.
ALL_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(0x0100, "poll", "always-on", "getRemoteCapability/getStatusFeature"),
    CommandSpec(0x0101, "poll", "always-on", "getDeviceVersion/getProperty"),
    CommandSpec(0x0102, "poll", "always-on", ""),
    CommandSpec(0x0103, "poll", "always-on", ""),
    CommandSpec(0x0104, "poll", "always-on", "getDeviceVersion, getProperty"),
    CommandSpec(0x0105, "poll", "cap-bit 0", ""),
    CommandSpec(0x0106, "poll", "cap-bit 1", ""),
    CommandSpec(0x0107, "poll", "cap-bit 2", ""),
    CommandSpec(0x0108, "poll", "cap-bit 3", "getKeyFunction"),
    CommandSpec(0x0109, "poll", "cap-bit 4", ""),
    CommandSpec(0x010B, "poll", "always-on", ""),
    CommandSpec(0x010C, "poll", "cap-bit 8", ""),
    CommandSpec(0x010D, "poll", "cap-bit 7", ""),
    CommandSpec(0x010F, "poll", "cap-bit 10", ""),
    CommandSpec(0x0110, "poll", "cap-bit 11", ""),
    CommandSpec(0x0111, "poll", "cap-bit 15", ""),
    CommandSpec(0x0112, "poll", "cap-bit 29", ""),
    CommandSpec(0x0115, "poll", "cap-bit 19", ""),
    CommandSpec(0x0116, "poll", "cap-bit 19", ""),
    CommandSpec(0x0118, "poll", "cap-bit 25", "getEarRestoreData"),
    CommandSpec(0x011A, "poll", "cap-bit 26", ""),
    CommandSpec(0x011E, "poll", "cap-bit 30", "getEarScanData"),
    CommandSpec(0x011F, "poll", "cap-bit 30", "getEarScanFilterData"),
    CommandSpec(0x0120, "poll", "cap-bit 32", "getEarLanguageData"),
    CommandSpec(0x0122, "poll", "cap-bit 34", "getAllEqInfo"),
    CommandSpec(0x0124, "poll", "cap-bit 38", ""),
    CommandSpec(0x0128, "poll", "cap-bit 46", ""),
    CommandSpec(0x0129, "poll", "ungated", "getStatusFeature"),
    CommandSpec(0x012A, "poll", "ungated", "detectFeature"),
    CommandSpec(0x012C, "poll", "cap-bit 54", "startSocketConnect"),
    CommandSpec(0x0131, "poll", "cap-bit 58", ""),
    CommandSpec(0x0200, "notify", "ungated", "getNotificationCapabilities"),
    CommandSpec(0x0201, "notify", "ungated", ""),
    CommandSpec(0x0202, "notify", "ungated", "registerNotification"),
    CommandSpec(0x0203, "notify", "ungated", "cancelRegisterNotification"),
    CommandSpec(0x0204, "notify", "ungated", "notificationEvent"),
    CommandSpec(0x0205, "notify", "cap-bit 22", "registerMultiNotification"),
    CommandSpec(0x0300, "OTA", "ungated", ""),
    CommandSpec(0x0301, "OTA", "ungated", ""),
    CommandSpec(0x0302, "OTA", "ungated", "readSize"),
    CommandSpec(0x0303, "OTA", "ungated", "readSize"),
    CommandSpec(0x0304, "OTA", "ungated", ""),
    CommandSpec(0x0305, "OTA", "ungated", ""),
    CommandSpec(0x0306, "OTA", "ungated", ""),
    CommandSpec(0x0307, "OTA", "ungated", ""),
    CommandSpec(0x0308, "OTA", "ungated", ""),
    CommandSpec(0x0400, "set", "cap-bit 5", "setFindMode"),
    CommandSpec(0x0401, "set", "cap-bit 3", "setKeyFunction"),
    CommandSpec(0x0402, "set", "cap-bit 6", ""),
    CommandSpec(0x0403, "set", "cap-bit 7", "setSwitchFeature"),
    CommandSpec(0x0404, "set", "cap-bit 8", "setNoiseReduction"),
    CommandSpec(0x0405, "set", "cap-bit 9", "setCompactnessDetect"),
    CommandSpec(0x0406, "set", "cap-bit 10", "setEQStatus"),
    CommandSpec(0x0407, "set", "cap-bit 11", "setHighVolume"),
    CommandSpec(0x0408, "set", "cap-bit 13", ""),
    CommandSpec(0x0409, "set", "cap-bit 14", ""),
    CommandSpec(0x040A, "set", "cap-bit 15", "setAutoPowerOffTime"),
    CommandSpec(0x040B, "set", "cap-bit 29", "setMultiConnectAction"),
    CommandSpec(0x040D, "set", "cap-bit 19", ""),
    CommandSpec(0x040E, "set", "cap-bit 19", "switchProcessHearingDetectionParams"),
    CommandSpec(0x0411, "set", "cap-bit 25", "setRestoreData"),
    CommandSpec(0x0412, "set", "cap-bit 26", "setPersonalizedNoiseReduction"),
    CommandSpec(0x0415, "set", "cap-bit 30", "sendProcessEarScanData"),
    CommandSpec(0x0418, "set", "cap-bit 34", "setEqInfo/getEqInfo"),
    CommandSpec(0x041B, "set", "cap-bit 38", "setBassEngineValue"),
    CommandSpec(0x041E, "set", "cap-bit 43", "setSpatialAudioType"),
    CommandSpec(0x0428, "set", "cap-bit 58", "setAIStatus/setTranslationAppStatus"),
    CommandSpec(0x042C, "set", "cap-bit 60", "setHeadsetMicStatus"),
    CommandSpec(0x0500, "device->app", "ungated", "requestHostTime"),
    CommandSpec(0x0502, "device->app", "ungated", "requestOperation"),
    CommandSpec(0x0503, "device->app", "ungated", "featureSwitchStatus"),
    CommandSpec(0x0504, "device->app", "ungated", "equalizerMode"),
    CommandSpec(0x0506, "device->app", "ungated", "customEq"),
    CommandSpec(0x0508, "device->app", "ungated", "bassEngineStatus"),
    CommandSpec(0x050E, "device->app", "cap-bit 46", "useTime"),
    CommandSpec(0x0516, "device->app", "ungated", "oneshotAudioData"),
    CommandSpec(0x0F00, "debug", "always-on", "switchDebugStatus"),
    CommandSpec(0x0F01, "debug", "ungated", ""),
    CommandSpec(0x0F02, "debug", "ungated", ""),
)

#: Every command ID in :data:`ALL_COMMANDS`.
COMMAND_IDS = frozenset(spec.cmd for spec in ALL_COMMANDS)

_COMMANDS_BY_ID = {spec.cmd: spec for spec in ALL_COMMANDS}


def command_spec(cmd: int) -> CommandSpec | None:
    """Return the :class:`CommandSpec` for ``cmd``, or ``None`` if unknown."""
    return _COMMANDS_BY_ID.get(cmd)


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


