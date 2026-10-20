# The TL protocol, as implemented in `tlcore`

This document describes the wire protocol `tlcore` speaks: how bytes are framed,
how a request is built, how a device reply is decoded, and exactly which commands
are implemented today. It is **derived from and checked against `tlcore`** — a
test (`tests/test_protocol_docs.py`) fails if a payload builder or decoder is
added to the library without being listed here.

The protocol was recovered by static analysis of the realme Link Android app,
then confirmed against a real Buds Air7 Pro over live SPP. Where this document
and `tlcore` disagree, `tlcore` wins; where `tlcore` and an external reference
codec disagree, the reference codec wins (see `tests/test_parity_tl_frame.py`).

**Verification labels used throughout:**

| Label | Meaning |
| --- | --- |
| **verified** | Exercised against real hardware and observed to work |
| **integrated** | Implemented and parity-checked, but not yet sent to a device |
| **known only** | The command ID is recorded for identification; no builder or decoder exists |

---

## 1. Transport

The TL protocol runs over **Bluetooth Classic RFCOMM/SPP**. There is no BLE/GATT
control path for the Air7 Pro — that was established experimentally, not assumed.
SPP is available on macOS, Windows, Linux, and Android, so the protocol can be
driven on any of them; it cannot work on iOS or in a browser.

A transport is a byte pipe. It has no knowledge of framing or commands; all of
that is in `tlcore` and is transport-independent.

## 2. Layer stack

```
application   Packet          [cmd:2 LE][type:1][len:2 LE][payload]
transport     OPPOv1Wrapper   [0xAA][LEB128 len][control][0x00][FSN?][payload]
```

Both layers are implemented in `tlcore/wire.py`. A single SPP write is one
transport frame; one application message may span several transport frames.

## 3. Transport framing (OPPOv1)

A frame is:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 1 | `SOF` = `0xAA` |
| 1 | 1–2 | Body length, **LEB128** varint (little-endian groups of 7 bits) |
| — | 1 | Control byte |
| — | 1 | Reserved = `0x00` |
| — | 1 | Frame sequence number, present only when control's low 2 bits ≠ 0 |
| — | n | Payload fragment |

There is **no checksum**. The control byte carries two flags and a frame status:

| Bit(s) | Meaning |
| --- | --- |
| `0x08` | flag A |
| `0x04` | flag B |
| `0x00` | frame status 0 — single frame, no FSN |
| `0x01` | frame status 1 — first frame of a multi-frame message |
| `0x02` | frame status 2 — middle frame |
| `0x03` | frame status 3 — last frame |

The declared body length counts the control byte, reserved byte, optional FSN,
and payload — everything after the varint. `build_frames(data, max_frame_size)`
splits a message into frames and `parse_frame` parses one; both live in
`tlcore.wire`. The `max_frame_size` is the transport MTU the device negotiated —
512 for the Air7 Pro. Frames are paced rather than sent back-to-back (about
10 ms apart for this model).

> **Known defect, reproduced deliberately.** At `max_frame_size == 130` the
> reference codec cannot round-trip a multi-frame body: it reserves a two-byte
> varint but writes one, shifting the read-back by two bytes. `tlcore` reproduces
> this byte for byte rather than diverging. The Air7 Pro uses MTU 512, so it is
> unaffected. The mechanism is documented in
> `tests/test_parity_tl_frame.py` (``check_known_frame_defect``).

## 4. Application layer (`Packet`)

A packet is a 5-byte little-endian header plus a payload:

| Field | Size | Notes |
| --- | --- | --- |
| command | 2 | `uint16` **little-endian** |
| type / flags | 1 | Opaque; preserved round-trip |
| payload length | 2 | `uint16` little-endian |
| payload | n | |

```python
>>> import tlcore
>>> tlcore.build_packet(0x0404, b"\x01\x01\x01").hex(" ")
'04 04 00 03 00 01 01 01'
```

The command ID splits into a **family** and a per-family code, and the top bit
marks a response:

```python
tlcore.cmd_class(cmd)   # cmd & 0x7F00  -> the manager family
tlcore.is_response(cmd) # cmd & 0x8000  -> True for device replies
```

> **The declared payload length is advisory on receive.** `parse_packet` slices
> the payload as “everything after the 5-byte header” and only records the
> declared length. The device relies on this: its `0x8118` reply declares
> `0xD820` but carries five payload bytes. Decoders must slice by bytes received;
> `parse_packet` reports such a packet as `truncated: True`.

`build_frames` turns a packet into transport frames; `StreamDecoder` turns an
arbitrarily-chunked SPP read (a partial frame, several frames, or a message split
across frames) into decoded packets. Callers working at the byte level can use
`parse_frame` / `parse_packet` directly.

## 5. Command families

| Family | Mask | Manager |
| --- | --- | --- |
| `0x0100` | poll | `PollCommandManager` — read device state |
| `0x0200` | notify | `NotificationCommandManager` — subscribe / push |
| `0x0300` | upgrade | `UpgradeManager` — OTA |
| `0x0400` | set | `SetCommandManager` — change a setting |
| `0x0500` | request | `RequestCommandManager` — device → app |
| `0x0F00` | debug | `DebugCommandManager` |

A response is the request with the top bit set (`0x0404` → `0x8404`).

The complete recovered catalogue is `tlcore.ALL_COMMANDS` — all **79** request
IDs with their family, capability gating and observed name — with
`tlcore.COMMAND_IDS` for membership tests and `tlcore.command_spec(cmd)` for
lookup. `CAPABILITY_TABLE` + `ALWAYS_ON` describe only the gated subset (48
IDs); `ALL_COMMANDS` also covers the ungated families (notify, OTA,
device→app, debug). [`command-coverage.md`](command-coverage.md) is the prose
view of the same table.

## 6. Capability negotiation

Most commands are gated: a device only accepts commands it has advertised. To
learn the set, send an **empty** `0x0100` and decode the `0x8100` body:

```
[status:1][bitmap: n bytes, least-significant bit first]
```

Bit *i* enables the *i*-th entry of `tlcore.CAPABILITY_TABLE`, each entry being
the command IDs in that capability group. The accepted set is the union of the
enabled groups plus `tlcore.ALWAYS_ON`:

```python
>>> caps = tlcore.parse_capabilities(bytes.fromhex("00bf4f5ae665dc4004"))
>>> caps["ok"], len(caps["commands"])
(True, 49)
```

That bitmap is what a real Buds Air7 Pro reports. Use `parse_capabilities` before
sending an optional command; the `ALWAYS_ON` set (`{0x0100, 0x0101, 0x0102,
0x0103, 0x0104, 0x010B, 0x0F00}`) needs no check.

`0x012A` is a different query — “which single feature does this device support”
— answered by an `0x812A` body decoded by `parse_feature_detect`.

Because the accepted set is a runtime property of the unit, no per-model
capability table is shipped. What *is* shipped is a static, tiered list of
models and the transport shape they use: see
[`device-compatibility.md`](device-compatibility.md).

## 7. Command surface

This is the set `tlcore` implements. A builder (`payload_*`) constructs a
request; a decoder (`parse_*`) interprets a reply or push. Commands with neither
are “known only” and are listed for identification only.

<!-- command-surface:start -->

| Command | Purpose | Builder | Decoder | Status |
| --- | --- | --- | --- | --- |
| `0x0404` | setNoiseReduction | `payload_set_noise_reduction`, `payload_set_noise_mode` | — | verified |
| `0x0401` | setKeyFunction (write the gesture table) | `payload_set_key_function` | — | verified |
| `0x0403` | setSwitchFeature | `payload_set_switch_feature` | — | integrated |
| `0x0418` | setEqInfo / getEqInfo | `payload_set_eq` | — | integrated |
| `0x0108` | getKeyFunction (read the gesture table) | — | `parse_key_function`, `parse_key_function_table` | verified |
| `0x0102` | getRemoteVendorId (remote vendor id) | — | `parse_remote_vendor_id` | verified |
| `0x0104` | getRemoteProtocolVersion (protocol-version exchange) | — | `parse_protocol_version` | verified |
| `0x0107` | getUpgradeCapability (OTA capability list) | — | `parse_upgrade_capability` | verified |
| `0x010B` | getEarbudsColorId | — | `parse_color_id` | verified |
| `0x0112` | getMultiConnect (paired-device / multipoint list) | — | `parse_multi_connect` | verified |
| `0x011A` | getPersonalizedNoiseReductionResultExist (personalized-noise result exists flag) | — | `parse_personalized_anc` | verified |
| `0x010F` | getEQStatus (read the sound-effect/EQ mode) | — | `parse_eq_mode` | verified |
| `0x0406` | setEQStatus (write the sound-effect/EQ mode) | `payload_set_eq_mode` | — | verified |
| `0x0110` | getHighVolume (read the loudness boost) | — | `parse_high_volume` | verified |
| `0x0407` | setHighVolume (write the loudness boost) | `payload_set_high_volume` | — | verified |
| `0x0405` | setCompactnessDetect (in-ear detect trigger; result on the `0x0204` event `0x04`) | `payload_compactness_detect` | `parse_compactness_event` | verified |
| `0x0131` | getAIStatus (read the AI / translation status) | — | `parse_ai_status` | verified |
| `0x0428` | setAIStatus / setTranslationAppStatus | `payload_set_ai_status` | — | verified |
| `0x0124` | bass engine (read) | — | `parse_bass_engine` | inferred |
| `0x041B` | setBassEngineValue (write) | `payload_set_bass_engine` | — | inferred |
| `0x8100` | capability report | — | `parse_capabilities` | verified |
| `0x812A` | detectFeature reply | — | `parse_feature_detect` | verified |
| `0x0204` | notification event (incl. button press `0xF1`) | — | `parse_notification_event`, `parse_public_event`, `parse_user_interaction` | verified |
| `0x8106` | battery | — | `parse_battery` | verified |
| `0x8109` | earbud / wear status | — | `parse_earbud_status` | verified |
| `0x8128` | use time | — | `parse_use_time` | verified |
| `0x8103` | model id | — | `parse_model_id` | verified |
| `0x8105` | firmware / component versions | — | `parse_device_version` | verified |
| `0x8122` | EQ info | — | `parse_eq_info` | verified |
| `0x810D` | switch / feature status | — | `parse_switch_features` | integrated |
| `0x8200` | getNotificationCapabilities (push-event types) | — | `parse_notification_capabilities` | verified |
| `0x0500` | device asks for the host time | `payload_host_time_reply` | `parse_host_time_request` | integrated |
| `0x0502` | device operation request | — | `parse_operation_request` | integrated |
| `0x0503` | feature-switch status sync | — | `parse_feature_switch_status` | integrated |
| `0x0504` | device changed EQ mode | — | `parse_eq_mode_request` | integrated |
| `0x0506` | custom-EQ list | — | `parse_custom_eq` | integrated |
| `0x0508` | bass-engine status | — | `parse_bass_engine_status` | integrated |
| `0x050E` | per-device use time | — | `parse_use_time_list` | integrated |
| `0x0516` | framed audio data from the device | — | `parse_oneshot_audio` | integrated |

Shared decoders that are not tied to one command:

| Name | Use |
| --- | --- |
| `parse_pairs` | Generic `[status][count][id, value]×count` TLV helper (used by the battery and wear decoders) |
| `decode_state` | Dispatches a `0x81xx` reply to its decoder via `STATE_DECODERS`, or returns `None` |
| `decode_request` | Dispatches a device → app `0x05xx` body to its decoder via `REQUEST_DECODERS`, or returns `None` |

<!-- command-surface:end -->

### Deliberately not implemented

Three recovered families are **documented here but never driven**: no builder,
decoder, sender or CLI command is provided for them, and the IDs are recorded
only so their traffic can be identified. The IDs are still listed in
`ALL_COMMANDS` (family and gating; most have no recovered name).

| Family | IDs | What it is | Why it is not implemented |
| --- | --- | --- | --- |
| hearing-test / ear-scan | `0x011E`, `0x011F`, `0x040E`, `0x0415` | a loud, repeated test-tone sweep | it plays **loud tones in the wearer's ears**; `send` refuses it without `--force` |
| OTA | `0x0300`–`0x0308` | a firmware **push** state machine — `0x0302` is `[fileOffset:4][fileData]` streamed from a local file | there is **no firmware-read command**, and the family is **ungated**, so a stray write could begin a flash. Never driven on hardware; `send` refuses it without `--force` |
| debug | `0x0F00`–`0x0F02` | a device → app **log stream** — `0x0F00` `[earSide][level][module]` starts it, `0x0F01` carries chunks, `0x0F02` stops it | it toggles real firmware verbosity; a retail Buds Air7 Pro **refuses it** (`0x8F00 status=1 total=0` for every documented variant). Never driven on hardware; `send` refuses it without `--force` |

Do not sweep unknown command space against a worn headset.

### Known but not implemented

Other recovered IDs have no builder or decoder. They are recorded for
identification; do not send them blind.

| Family | IDs | Why |
| --- | --- | --- |
| set | the `0x04xx` IDs not in the table above (minus the tone family) | ID + gating known, payload layout not recovered |
| poll | the `0x01xx` IDs not in the table above | ID + gating known, reply layout not mapped |

All eight device → app `0x05xx` IDs have a decoder (see §8).

The Air7 Pro advertises the capability bit that gates `0x0403`/`0x010D`, and both
are implemented; it simply reports no switch features, so its boolean toggles go
through the dedicated set commands instead. The probe is recorded in
[`live-test.md`](live-test.md). The full known-vs-implemented command catalogue is
in [`command-coverage.md`](command-coverage.md).

## 8. Reading device state

Device state arrives two ways and both share the same TLV shapes, so the parsers
are shared:

* as a reply to a poll (`0x81xx`), or
* as an unsolicited push (`0x0204`, event byte first).

The usual TLV convention is `[status-or-event][count][id, value]×count` with ids
`1/2/3 = left/right/case`.

```python
>>> tlcore.parse_battery(bytes.fromhex("0003016402640300"))
{'status': 0, 'devices': {'L': {'level': 100, 'charging': False},
                          'R': {'level': 100, 'charging': False},
                          'CASE': {'level': 0, 'charging': False}}}
```

In a battery value byte, bit 7 is the charging flag and bits 0–6 are the level.
A **case level of 0 means the buds are out of the case**, not an empty battery.

Wear state: only the value `3` (`tlcore.WEAR_IN_EAR`) is confirmed, and it is the
in-ear gate that noise-mode writes depend on. The other values are deliberately
left unnamed rather than guessed.

### Noise mode

The six mode values were confirmed **by ear** on the target device and are not a
static inference:

| Value | Mode |
| --- | --- |
| `0x01` | Normal (noise off) |
| `0x02` | Transparency |
| `0x04` | ANC light |
| `0x08` | ANC deep |
| `0x10` | ANC medium |
| `0x20` | ANC smart (adaptive) |

Read the active mode with `0x010C` plus selector `01` → `0x810C 00 01 01 <V>`;
write it with `0x0404 action=1` (the `payload_set_noise_mode` wrapper). The write
is accepted **only while the buds are worn**; otherwise the device answers
`0x8404 0e` (`tlcore.NotWorn`). Always read the mode back to confirm: the
asynchronous `0x0204` push is racy and is a hint, not the source of truth. The
read-back body has no standalone decoder; the mode is its final byte (see
`Session.read_noise_mode`).

`payload_set_noise_reduction(action, type, value)` is the general form:
`action=2` writes the noise *cycle* settings instead of the active mode.

### EQ / sound effect

The sound-effect preset is a plain mode value. Read it with `0x010F` →
`0x810F 00 <mode>` (`parse_eq_mode`); write it with `0x0406`
(`payload_set_eq_mode`). **Verified live** on the Air7 Pro: the write value is
read back **identity** over `0..6`, and values `>= 7` clamp to `6`:

| `0x0406` written | `0x810F` read back | `0x0122` `isSelected` |
| --- | --- | --- |
| `0`..`3` | `0`..`3` | `0` |
| `4` | `4` | `1` |
| `5`, `6` | `5`, `6` | `0` |
| `7`, `8` | `6` (clamped) | `0` |

Restoring the active preset therefore needs **no mapping table** — it is
`0x010F` in, `0x0406` out. (The vendor app's `SettingsInfo` enum maps
`{1,2,4,8} → {1,0,2,3}`, but that is an app-side convenience; the wire mode is
the raw value.)

`0x0122` (`getAllEqInfo`) returns the preset/custom list and is decoded by
`parse_eq_info`::

    [status][count] + [isSelected][minValue][maxValue][eqId][nameLen][name utf8]
                      [freqCount][ (freq uint16 LE)(db int8) x freqCount ]

The Air7 Pro reports one point-less entry — `00 01 01 01 02 04 00 00` → eqId
`4`, `min=1`, `max=2`, no bands — and `isSelected` is `1` only while the mode is
`4`. Custom curves are read back faithfully but **not** written: the `0x0418`
encoder refuses a zero-band record, and the read byte is `isSelected` while the
write's first byte is `action`, so the bytes cannot be replayed verbatim. Backup
captures the list and leaves it `not_restored`.

### High volume (loudness boost)

`0x0407` is a one-byte boolean write and `0x0110` is its read half. The reply is
`0x8110 [status][0|1]` (`parse_high_volume`); write it with
`payload_set_high_volume`, or use the `Session.read_high_volume` /
`Session.set_high_volume` pair. **Verified live** on the Air7 Pro (2026-10-20):
`0x0407 00` moved the read-back from `00 01` to `00 00`, and `0x0407 01`
restored it. The device answers `0x8407 <status>` and additionally pushes
`0x0204 0f 08 00 <value>` — event `0x0F` ("publicEvent"), a 2-byte little-endian
flag word (`0x0008`, bit 3 = volume level) followed by the value. The same push
reports AI state as `0f 02 00 <state> <type>` (bit 1).
Like every other settings write it is accepted only while the buds are worn
(in-ear); otherwise the device replies `0x8407 0e`.

### In-ear (compactness) detection

`0x0405` starts (`01`) or stops (`00`) the in-ear / compactness sensor with a
single action byte (`payload_compactness_detect`). It has **no read-back pair**:
the firmware answers with `0x8405 <status>` and reports the result on the
`0x0204` **event `0x04`** push. That body is two `[earSide][value]` records --
`parse_compactness_event` names the sides (`0x01` left, `0x02` right) and leaves
`value` as the raw status code. **Verified live** on the Air7 Pro (2026-10-20):
`0x0405 01` pushed `04 01 04 02 04` and, on a second run, `04 01 00 02 00`, so
`value` is a live status code, not a constant; `0x0405 00` stops it. Use
`Session.start_compactness_detect` / `stop_compactness_detect`, or
`tlcli compactness start|stop`, which listens for the push. The trigger is a
sensor read -- it drives no audio.

### Multipoint (paired-device list)

`0x0112` returns the devices paired to the buds. The reply is
`[status][count]` followed by `count` records (`CommandUtil.h`)::

    [mac:6][length:1][state:1][flag:1][nameLen:1][name:nameLen]

`length == nameLen + 3` is the record stride minus 7, and the name is
NUL-terminated. The MAC is stored **least-significant byte first** and
`ProtocolUtil.b(..., true)` reverses it, so `parse_multi_connect` returns `mac`
in display order. **Verified live** on the Air7 Pro (2026-10-20): 167 bytes, 7
entries, all bytes consumed. `state`/`flag` are byte fields whose meaning is
**inferred** (the connected/primary entry was `state=2, flag=1`; the rest `0,0`).
Use `Session.read_multi_connect`, or `tlcli multipoint`. The list contains the
host's real device names, so treat a capture as private.

### AI / translation status

`0x0428` is a two-byte `[state][type]` write (`payload_set_ai_status`) and
`0x0131` is its read half -- `0x8131 [status][state][type]` (`parse_ai_status`).
`state` is `AIStatus` (0 = off, 1 = on) and `type` is `AIType`
(`CommandProtocol` values 0, 1, 2, 4). **Verified live** on the Air7 Pro
(2026-10-20): `0x0428 01 01` is accepted (`0x8428 00`), the device pushes
`0x0204 0f 02 00 01 01` and the `0x0131` read-back becomes `00 01 01`;
`01 02` gives `00 01 02`. `state=0` deactivates, but **only with the matching
active `type`** -- `00 01` is ignored while type 2 is active, and vice versa.
`type=4` ("AI chat") is accepted at the protocol level but **not applied** by
firmware `1.1.0.85`: the push and the read both stay `00 00`. The AI state is
**session-scoped** (it resets when the link closes), so an app-free client
cannot leave the buds in a stale AI state. Use `Session.read_ai_status` /
`Session.set_ai_status`, or `tlcli ai-status get|set <state> <type>`. The same
`0x0204 0f` push is decoded by `parse_public_event`.

### Buttons and gestures

A headset stores a **key-function table** binding a gesture on an earbud to a
function. Read it with an empty `0x0108`, write it with `0x0401`; both carry the
same 4-byte record (`tlcore.KeyFunctionInfo`)::

    [deviceType][button][action][function]

`parse_key_function_table` returns the records as `KeyFunctionInfo` entries plus
the labels from `DEVICE_TYPE_NAMES`, `ACTION_NAMES` and `FUNCTION_NAMES`. The
Air7 Pro reports 10 entries (5 gestures x left/right):

```python
>>> entry = tlcore.parse_key_function_table(
...     bytes.fromhex("000a0201010002010201020103060201040802010600"))["entries"][2]
>>> entry
KeyFunctionInfo(device_type=2, button=1, action=3, function=6)
>>> entry.describe()
'right triple-click -> next-track'
```

A **press** is not a `0x8108` frame: the device pushes a `0x0204` notification
whose first byte is the event type `0xF1` (`tlcore.EVENT_USER_INTERACTION`). Pass
the body (after the type byte) to `parse_user_interaction`::

    [deviceType][button][buttonAction][function][scene] + options(uint16 LE)

The write is whole-table, so a client reads `0x0108`, patches one
`KeyFunctionInfo`, and writes the full table back with `payload_set_key_function`.
Two pure helpers make that safe: `patch_key_functions(entries, updates)` changes
only the `(deviceType, button, action)` rows named in `updates`, and
`disable_long_press(entries)` sets every long-press row to `0`. A
`tlcore.Session` wraps the commands as `read_key_function()` (returns the parsed
table) and `set_key_function(entries)` (writes it back).

A `function` of `0` means **off** (`FUNCTION_NAMES[0]`): setting a gesture to
`0` disables it. Long press is the records whose `action` is `4` or `5`, and
`0` is a menu-valid function for them. Redirecting every such record to `0`
(a whole-table write) therefore turns long press off; a real Buds Air7 Pro
accepted it (`0x8401 status=0`) and read the change back, and the original table
restored cleanly.

### Device -> app requests (`0x05xx`)

These are unsolicited requests the *device* makes of the host;
`tlcore.decode_request(cmd, payload)` dispatches them via `REQUEST_DECODERS`.
Only `0x0500` expects a reply. All multi-byte fields are little-endian.

| Command | Body | Decoder |
| --- | --- | --- |
| `0x0500` | asks the host for the time; answer `0x8500` with `payload_host_time_reply(unix)` | `parse_host_time_request` |
| `0x0502` | `[action]` (5 -> app message 54, 6 -> 55) | `parse_operation_request` |
| `0x0503` | `[count] + count x [featureType][status]` | `parse_feature_switch_status` |
| `0x0504` | `[eqMode]` | `parse_eq_mode_request` |
| `0x0506` | custom-EQ list | `parse_custom_eq` |
| `0x0508` | `[min][max][current]` + optional `[type]` | `parse_bass_engine_status` |
| `0x050E` | `[count] + count x [deviceType][seconds:4 LE]` | `parse_use_time_list` |
| `0x0516` | `[frameNum:2 LE][channel:2 LE] + frames{seqNum:4 LE, dataSize:2 LE, data}` | `parse_oneshot_audio` |

## 9. Errors

All exceptions derive from `tlcore.TLError`. Wire-level `FrameError` and
`PacketError` also subclass `ValueError` for compatibility with the reference
codec. Field-relevant types include:

| Exception | Raised when |
| --- | --- |
| `DeviceRejected` | The device answered with a non-zero status |
| `NotWorn` | A noise write was refused because the buds are not in an ear (`0x8404 0e`) |
| `ChannelRefused` | The RFCOMM channel was refused while the link is up |
| `PageTimeout` | The device did not answer — asleep, out of range, or off |
| `Timeout` | A read or write did not complete in time |

## 10. Driving the protocol

Three objects turn the above into a working client. None of the protocol logic
lives in a platform module, which is what lets the same code run on hardware and
over a recording:

* **`tlcore.Transport`** — a six-method byte pipe (`discover_channel`, `open`,
  `write`, `read`, `close`, `is_connected`). Implementations live in
  `tltransport`: `MacOSTransport` (IOBluetooth RFCOMM) and `ReplayTransport`
  (offline, over `fixtures/*.jsonl`).
* **`tlcore.Session`** — frames a command onto a transport, decodes replies,
  keeps unsolicited `0x0204` pushes separate in `Session.events`, negotiates the
  capability bitmap (``negotiate_capabilities`` / ``supports``), and applies the
  in-ear gate (``set_noise_mode`` raises :class:`~tlcore.errors.NotWorn`).
  ``read_status`` polls model, firmware, battery, wear, mode, and use time.
* **`tlcli`** — a command-line front end (`python -m tlcli`) over both. It is the
  first consumer of the core and the hardware test harness.
* **`tlapp`** — a PySide6 desktop app (`python -m tlapp`) over the same two
  layers, built one verified feature at a time. Its controller is Qt-free, so
  the device half is testable without a display; see [`app.md`](app.md).

Commands below assume the repository virtualenv is active (see
[`../AGENTS.md`](../AGENTS.md)).

```console
$ python -m tlcli --replay fixtures/spp-smoketest.jsonl info
$ python -m tlcli --address AA-BB-CC-DD-EE-FF mode set anc
$ python -m tlcli --address AA-BB-CC-DD-EE-FF send 0x010C 01 --read 1
```

The replay transport means the entire stack can be exercised with no device
present, which is how `tests/test_transport_and_session.py` runs in CI. To run
against a real headset, follow [`live-test.md`](live-test.md).

### Backup and restore

`tlcore.backup` turns the above into a settings backup. There is no single
"read all settings" command, so, like the vendor app, it negotiates the
capability bitmap and polls a curated set of status commands, storing each raw
reply:

```python
import tlcore

snapshot = tlcore.capture(session)          # reads; safe
report = tlcore.restore(session, snapshot)  # writes only what it can
```

Restore re-applies the four settings whose read and write forms are recovered
and symmetric -- **noise mode** (`0x010C`/`0x0404`), **key function**
(`0x0108`/`0x0401`), **EQ mode** (`0x010F`/`0x0406`), **switch features**
(`0x010D`/`0x0403`) -- and reports the rest as `not_restored` rather than
guessing at payloads it does not know.

```console
$ python -m tlcli --address AA-BB-CC-DD-EE-FF backup settings.json
$ python -m tlcli --address AA-BB-CC-DD-EE-FF restore --dry-run settings.json
$ python -m tlcli --address AA-BB-CC-DD-EE-FF restore settings.json
```

## 11. What this document does not cover

The remaining command IDs (OTA `0x03xx`, most `0x04xx` settings, most payload
layouts) are recovered but not transcribed or implemented. They are all
tabulated row by row in [`command-coverage.md`](command-coverage.md); nothing
about them is claimed here. `tlcore` is the authority for what is and is not
implemented, and the drift-guard test keeps this document honest.
