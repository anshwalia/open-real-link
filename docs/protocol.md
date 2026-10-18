# The TL protocol, as implemented in `tlcore`

This document describes the wire protocol `tlcore` speaks: how bytes are framed,
how a request is built, how a device reply is decoded, and exactly which commands
are implemented today. It is **derived from and checked against `tlcore`** — a
test (`tests/test_protocol_docs.py`) fails if a payload builder or decoder is
added to the library without being listed here.

The protocol was recovered by static analysis of the realme Link Android app,
then confirmed against a real Buds Air7 Pro over live SPP. The full
reverse-engineering record — every command ID, decompiled method, and evidence
item — lives in a separate, non-public analysis repository. Where this document
and `tlcore` disagree, `tlcore` wins; where `tlcore` and the original reference
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

## 7. Command surface

This is the set `tlcore` implements. A builder (`payload_*`) constructs a
request; a decoder (`parse_*`) interprets a reply or push. Commands with neither
are “known only” and are listed for identification only.

<!-- command-surface:start -->

| Command | Purpose | Builder | Decoder | Status |
| --- | --- | --- | --- | --- |
| `0x0404` | setNoiseReduction | `payload_set_noise_reduction`, `payload_set_noise_mode` | — | verified |
| `0x0401` | setKeyFunction | `payload_set_key_function` | — | integrated |
| `0x0418` | setEqInfo / getEqInfo | `payload_set_eq` | — | integrated |
| `0x8100` | capability report | — | `parse_capabilities` | verified |
| `0x812A` | detectFeature reply | — | `parse_feature_detect` | verified |
| `0x0204` | notification event | — | `parse_notification_event` | verified |
| `0x8106` | battery | — | `parse_battery` | verified |
| `0x8109` | earbud / wear status | — | `parse_earbud_status` | verified |
| `0x8128` | use time | — | `parse_use_time` | verified |
| `0x8103` | model id | — | `parse_model_id` | verified |
| `0x8105` | firmware / component versions | — | `parse_device_version` | verified |
| `0x8122` | EQ info | — | `parse_eq_info` | verified |

Shared decoders that are not tied to one command:

| Name | Use |
| --- | --- |
| `parse_pairs` | Generic `[status][count][id, value]×count` TLV helper (used by the battery and wear decoders) |
| `decode_state` | Dispatches a `0x81xx` reply to its decoder via `STATE_DECODERS`, or returns `None` |

<!-- command-surface:end -->

### Deliberately not implemented

Some recovered commands drive **loud, repeated test tones** in the wearer's ears
(the hearing-test / ear-scan family: `0x011E`, `0x011F`, `0x040E`, `0x0415`).
Their IDs are recorded so traffic can be identified, but no builder or decoder is
provided. Do not sweep unknown command space against a worn headset.

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

```console
$ python -m tlcli --replay fixtures/spp-smoketest.jsonl info
$ python -m tlcli --address AA-BB-CC-DD-EE-FF mode set anc
$ python -m tlcli --address AA-BB-CC-DD-EE-FF send 0x010C 01 --read 1
```

The replay transport means the entire stack can be exercised with no device
present, which is how `tests/test_transport_and_session.py` runs in CI. To run
against a real headset, follow [`live-test.md`](live-test.md).

## 11. What this document does not cover

The remaining command IDs (OTA `0x03xx`, most `0x04xx` settings, most payload
layouts) are recovered but not transcribed or implemented. They are catalogued in
the analysis repository; nothing about them is claimed here. `tlcore` is the
authority for what is and is not implemented, and the drift-guard test keeps this
document honest.
