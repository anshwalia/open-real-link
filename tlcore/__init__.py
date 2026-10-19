"""tlcore -- the TL headset protocol, recovered from the realme Link app.

Pure protocol, **no I/O**.  Nothing in this package imports a platform module, a
socket, a serial port or a GUI toolkit; that is what keeps it portable and
testable.  Transports live in ``tltransport``, the UI in ``tlcli``/``app``.

Distribution name is ``open-real-link``; ``tlcore`` is the import name.

Layering, innermost first::

    wire.py       OPPOv1 transport framing  +  TL packet header  +  StreamDecoder
    commands.py   command ids, manager classes, capability bitmap
    noise.py      the six noise-mode values and the read selectors
    buttons.py    key-function tuples, gesture/function labels, press events
    payloads.py   payload builders for the commands recovered in full
    decoders.py   battery / wear / use-time / events / capability bodies
    requests.py   the device -> app request family (0x05xx)
    devices.py    static per-model compatibility table (candidate list, no I/O)

Everything here was validated against a real Buds Air7 Pro over live SPP.  The
``tests/parity_*`` harness cross-checks the codec against an external reference
codec supplied via ``TL_REFERENCE`` and fails if the bytes ever diverge.
"""

from __future__ import annotations

from .backup import BACKUP_FORMAT, READ_PLAN, capture, restore
from .buttons import (
    ACTION_NAMES,
    DEVICE_TYPE_NAMES,
    EVENT_USER_INTERACTION,
    FUNCTION_NAMES,
    KeyFunctionInfo,
    parse_key_function_table,
    parse_user_interaction,
)
from .commands import (
    ALL_COMMANDS,
    ALWAYS_ON,
    CAPABILITY_TABLE,
    CLASS_DEBUG,
    CLASS_MASK,
    CLASS_NAMES,
    CLASS_NOTIFY,
    CLASS_POLL,
    CLASS_REQUEST,
    CLASS_SET,
    CLASS_UPGRADE,
    CMD_BASS_ENGINE_STATUS,
    CMD_CANCEL_REGISTER_NOTIFICATION,
    CMD_CUSTOM_EQ,
    CMD_DETECT_FEATURE,
    CMD_EQ_INFO,
    CMD_EQUALIZER_MODE,
    CMD_FEATURE_SWITCH_STATUS,
    CMD_GET_ALL_EQ_INFO,
    CMD_GET_BASS_ENGINE,
    CMD_GET_EQ_MODE,
    CMD_GET_KEY_FUNCTION,
    CMD_GET_NOISE_REDUCTION,
    CMD_GET_REMOTE_CAPABILITY,
    CMD_GET_STATUS_FEATURE,
    CMD_NAMES,
    CMD_NOTIFICATION_CAPS,
    CMD_NOTIFICATION_EVENT,
    CMD_ONESHOT_AUDIO_DATA,
    CMD_REGISTER_NOTIFICATION,
    CMD_REQUEST_HOST_TIME,
    CMD_REQUEST_OPERATION,
    CMD_SET_BASS_ENGINE,
    CMD_SET_EQ_MODE,
    CMD_SET_KEY_FUNCTION,
    CMD_SET_NOISE_REDUCTION,
    CMD_SET_SWITCH_FEATURE,
    CMD_USE_TIME,
    COMMAND_IDS,
    RESPONSE_BIT,
    CommandSpec,
    cmd_class,
    command_spec,
    is_response,
    parse_capabilities,
)
from .decoders import (
    CASE_ID,
    DEVICE_NAMES,
    EVENT_TYPE_NAMES,
    STATE_DECODERS,
    WEAR_IN_EAR,
    decode_state,
    parse_bass_engine,
    parse_battery,
    parse_device_version,
    parse_earbud_status,
    parse_eq_info,
    parse_eq_mode,
    parse_feature_detect,
    parse_key_function,
    parse_model_id,
    parse_notification_event,
    parse_pairs,
    parse_switch_features,
    parse_use_time,
)
from .devices import (
    KNOWN_DEVICES,
    MODEL_IDS,
    DeviceSpec,
    device_spec,
    devices_for_model,
)
from .errors import (
    ChannelRefused,
    DeviceRejected,
    FrameError,
    NotConnected,
    NotWorn,
    PacketError,
    PageTimeout,
    Timeout,
    TLError,
    TransportError,
)
from .noise import (
    NOISE_FIELD_CYCLE,
    NOISE_FIELD_MODE,
    NOISE_MODE_ANC,
    NOISE_MODE_ANC_DEEP,
    NOISE_MODE_ANC_LIGHT,
    NOISE_MODE_ANC_MEDIUM,
    NOISE_MODE_ANC_SMART,
    NOISE_MODE_NAMES,
    NOISE_MODE_NORMAL,
    NOISE_MODE_TRANSPARENCY,
)
from .payloads import (
    crc16_ccitt,
    payload_set_bass_engine,
    payload_set_eq,
    payload_set_eq_mode,
    payload_set_key_function,
    payload_set_noise_mode,
    payload_set_noise_reduction,
    payload_set_switch_feature,
)
from .requests import (
    OPERATION_MESSAGES,
    REQUEST_DECODERS,
    decode_request,
    parse_bass_engine_status,
    parse_custom_eq,
    parse_eq_mode_request,
    parse_feature_switch_status,
    parse_host_time_request,
    parse_oneshot_audio,
    parse_operation_request,
    parse_use_time_list,
    payload_host_time_reply,
)
from .session import Session
from .transport import Transport
from .wire import (
    SOF,
    StreamDecoder,
    build_frames,
    build_packet,
    deframe,
    parse_frame,
    parse_packet,
    varint_decode,
    varint_encode,
)

__version__ = "0.0.1"

__all__ = [
    # wire
    "SOF", "StreamDecoder", "build_frames", "build_packet", "deframe",
    "parse_frame", "parse_packet", "varint_decode", "varint_encode",
    # commands
    "ALL_COMMANDS", "ALWAYS_ON", "CAPABILITY_TABLE", "CLASS_DEBUG", "CLASS_MASK", "CLASS_NAMES",
    "CLASS_NOTIFY", "CLASS_POLL", "CLASS_REQUEST", "CLASS_SET", "CLASS_UPGRADE",
    "CMD_CANCEL_REGISTER_NOTIFICATION", "CMD_BASS_ENGINE_STATUS", "CMD_CUSTOM_EQ", "CMD_DETECT_FEATURE",
    "CMD_EQ_INFO", "CMD_EQUALIZER_MODE", "CMD_FEATURE_SWITCH_STATUS", "CMD_GET_ALL_EQ_INFO",
    "CMD_GET_BASS_ENGINE", "CMD_GET_EQ_MODE", "CMD_GET_KEY_FUNCTION", "CMD_GET_NOISE_REDUCTION",
    "CMD_GET_REMOTE_CAPABILITY", "CMD_GET_STATUS_FEATURE",
    "CMD_NAMES", "CMD_NOTIFICATION_CAPS", "CMD_NOTIFICATION_EVENT", "CMD_ONESHOT_AUDIO_DATA",
    "CMD_REGISTER_NOTIFICATION", "CMD_REQUEST_HOST_TIME", "CMD_REQUEST_OPERATION",
    "CMD_SET_BASS_ENGINE", "CMD_SET_EQ_MODE", "CMD_SET_KEY_FUNCTION", "CMD_SET_NOISE_REDUCTION",
    "CMD_SET_SWITCH_FEATURE", "CMD_USE_TIME",
    "COMMAND_IDS", "CommandSpec",
    "RESPONSE_BIT",
    "cmd_class", "command_spec", "is_response", "parse_capabilities",
    # noise
    "NOISE_FIELD_CYCLE", "NOISE_FIELD_MODE", "NOISE_MODE_ANC", "NOISE_MODE_ANC_DEEP",
    "NOISE_MODE_ANC_LIGHT", "NOISE_MODE_ANC_MEDIUM", "NOISE_MODE_ANC_SMART",
    "NOISE_MODE_NAMES", "NOISE_MODE_NORMAL", "NOISE_MODE_TRANSPARENCY",
    # payloads
    "crc16_ccitt", "payload_set_bass_engine", "payload_set_eq", "payload_set_eq_mode",
    "payload_set_key_function",
    "payload_set_noise_mode", "payload_set_noise_reduction", "payload_set_switch_feature",
    # decoders
    "CASE_ID", "DEVICE_NAMES", "EVENT_TYPE_NAMES", "STATE_DECODERS", "WEAR_IN_EAR",
    "decode_state", "parse_bass_engine", "parse_battery", "parse_device_version",
    "parse_earbud_status",
    "parse_eq_info", "parse_eq_mode", "parse_feature_detect", "parse_key_function", "parse_model_id",
    "parse_notification_event", "parse_pairs", "parse_switch_features", "parse_use_time",
    # buttons / key functions
    "ACTION_NAMES", "DEVICE_TYPE_NAMES", "EVENT_USER_INTERACTION", "FUNCTION_NAMES",
    "KeyFunctionInfo", "parse_key_function_table", "parse_user_interaction",
    # device -> app requests (0x05xx)
    "OPERATION_MESSAGES", "REQUEST_DECODERS", "decode_request", "payload_host_time_reply",
    "parse_bass_engine_status", "parse_custom_eq", "parse_eq_mode_request",
    "parse_feature_switch_status", "parse_host_time_request", "parse_oneshot_audio",
    "parse_operation_request", "parse_use_time_list",
    # errors
    "TLError", "FrameError", "PacketError", "DeviceRejected", "NotWorn",
    "TransportError", "NotConnected", "ChannelRefused", "PageTimeout", "Timeout",
    # session
    "Session", "Transport",
    # backup / restore
    "BACKUP_FORMAT", "READ_PLAN", "capture", "restore",
    # devices
    "KNOWN_DEVICES", "MODEL_IDS", "DeviceSpec", "device_spec", "devices_for_model",
]
