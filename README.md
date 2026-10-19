# open-real-link

App-free control of **realme / OPPO Bluetooth headsets** over the "TL" protocol,
recovered by reverse engineering the realme Link Android app.

> **Not affiliated with, endorsed by, or supported by realme or OPPO.**
> "realme", "OPPO" and "realme Link" are trademarks of their respective owners
> and are used here only to describe what this software interoperates with. No
> vendor code, binary, or asset is redistributed.

## Overview

`open-real-link` talks to realme/OPPO TL-series earbuds directly over Bluetooth
Classic RFCOMM/SPP — the same channel the official app uses — so the headset can
be controlled without the vendor app, an account, or a phone.

The protocol core, `tlcore`, is byte-for-byte verified: it produces and parses
the same bytes as a reference implementation that was validated on real
hardware. It is wrapped by platform transports and a command-line front end;
see [Status](#status) below.

## Status

The recovered protocol core, `tlcore`, is byte-for-byte verified. The macOS
transport, an offline replay transport, and a command-line front end are
implemented; the graphical application is still to be built.

A settings **backup and restore** path (`tlcli backup` / `tlcli restore`) sits on
top of the read-side decoders. Because the protocol has no "read all settings"
command, it polls the supported status commands and stores each raw reply;
restore re-applies only the settings whose read and write forms are both
recovered (noise mode, key function, EQ mode, switch features), and reports the
rest as unrestorable rather than guessing. It is
exercised offline against fixtures.

The transport and session layers are exercised offline against recorded fixtures
(`tests/test_transport_and_session.py`), and the macOS transport has been run
end to end against a real Buds Air7 Pro (`python -m tlcli smoke`, 2026-10-18).

Verified on hardware (realme Buds Air7 Pro):

| Capability | Status |
| --- | --- |
| Read active noise mode | Verified |
| Set active noise mode | Verified end to end |
| Read/write sound-effect (EQ) mode | Verified end to end |
| Read battery, wear state, firmware, model, and use time | Verified |
| Read capability bitmap → supported-command set | Verified |
| Encode/decode parity with the reference codec | Verified (23,162 checks) |

Noise-mode values, confirmed by listening on the target device:

| Value | Mode |
| --- | --- |
| `0x01` | Normal (noise off) |
| `0x02` | Transparency |
| `0x04` | ANC light |
| `0x08` | ANC deep |
| `0x10` | ANC medium |
| `0x20` | ANC smart (adaptive) |

> A mode change is accepted only while the earbuds are worn. Otherwise the
> device rejects it. Always read the mode back to confirm the change; the
> asynchronous status push can arrive out of order and should be treated as a
> hint, not the source of truth.

## Compatibility

The TL protocol runs over Bluetooth Classic RFCOMM/SPP. Because SPP is available
on **macOS, Windows, Linux, and Android**, the protocol can be driven on any of
them; it cannot work on iOS or in a web browser, which do not expose SPP. The
macOS transport is implemented; Linux, Windows, and Android transports are still
to be written.

## Safety

Three recovered command families are **documented but not implemented** — no
builder, decoder, or CLI command is provided for any of them, and their IDs are
recorded only so their traffic can be identified:

* the **hearing-test / ear-scan** family (`0x011E`, `0x011F`, `0x040E`, `0x0415`)
  drives **loud, repeated test tones** in the wearer's ears;
* the **OTA** family (`0x0300`–`0x0308`) is a firmware **push** state machine with
  **no read-back** path, and it is ungated, so a stray write could start a flash;
* the **debug** family (`0x0F00`–`0x0F02`) toggles real firmware verbosity and is
  refused by retail units.

Do not sweep unknown commands against a headset that is being worn; any extension
of this project should gate writes behind an explicit opt-in. See
[`docs/protocol.md`](docs/protocol.md) §7 for the recorded shapes and the
deliberate non-implementation rationale.

## Installation

The package is **not yet published to PyPI**. Install from source:

```console
$ git clone https://github.com/anshwalia/open-real-link.git
$ cd open-real-link
$ pip install .
```

The protocol core has no dependencies and imports no platform-specific module,
so it can be used and tested anywhere, including against recorded captures.

Platform transports are optional extras, installed the same way:

```console
$ pip install ".[macos]"   # macOS (implemented)
$ pip install ".[linux]"   # Linux (transport still to be written)
```

For development (editable install plus test and lint tools):

```console
$ pip install -e ".[dev]"
```

## Quick start

```console
$ python -c "import tlcore; print(tlcore.NOISE_MODE_NAMES)"
{1: 'Normal (noise off)', 2: 'Transparency', 4: 'ANC light', 8: 'ANC deep', 16: 'ANC medium', 32: 'ANC smart'}
```

Build a command and frame it for the device:

```python
import tlcore

packet = tlcore.build_packet(
    tlcore.CMD_SET_NOISE_REDUCTION,
    tlcore.payload_set_noise_mode(tlcore.NOISE_MODE_ANC),
)
for frame in tlcore.build_frames(packet, max_frame_size=512):
    ...  # write frame to the transport, ~10 ms apart for the Air7 Pro
```

Or drive a headset from the command line (run from a checkout):

```console
$ python -m tlcli --replay fixtures/spp-smoketest.jsonl info
$ python -m tlcli --address AA-BB-CC-DD-EE-FF smoke      # read-only live check
$ python -m tlcli --address AA-BB-CC-DD-EE-FF mode set anc
$ python -m tlcli --address AA-BB-CC-DD-EE-FF eq          # read the EQ mode + list
$ python -m tlcli --address AA-BB-CC-DD-EE-FF keys        # read the gesture table
$ python -m tlcli --address AA-BB-CC-DD-EE-FF events --seconds 15   # decode pushes
$ python -m tlcli say "Noise control is on."            # host-side TTS via the headset
$ python -m tlcli --address AA-BB-CC-DD-EE-FF say --status   # speak a live summary
$ python -m tlcli --address AA-BB-CC-DD-EE-FF send 0x010C 01 --read 1
$ python -m tlcli --address AA-BB-CC-DD-EE-FF backup settings.json
$ python -m tlcli --address AA-BB-CC-DD-EE-FF restore --dry-run settings.json
```

See [`docs/live-test.md`](docs/live-test.md) for a hardware runbook and
troubleshooting.

## Project layout

| Path | Contents |
| --- | --- |
| `tlcore/` | Protocol core: framing, packets, commands, payloads, decoders, backup/restore |
| `tltransport/` | `MacOSTransport` (IOBluetooth RFCOMM) and `ReplayTransport` (offline) |
| `tlcli/` | Command-line front end, `python -m tlcli` |
| `fixtures/` | Recorded device traffic used as golden test input |
| `tests/` | Reference-codec parity, transport/session, macOS transport, and protocol-docs guards |
| `docs/` | Protocol reference and design notes |

## Documentation

Additional documentation lives in [`docs/`](docs/):

- [`protocol.md`](docs/protocol.md) — the wire format, command set, and which
  commands are implemented and verified
- [`command-coverage.md`](docs/command-coverage.md) — every known TL command ID,
  with what `open-real-link` implements and what an Air7 Pro accepts
- [`device-compatibility.md`](docs/device-compatibility.md) — the static
  candidate list of models, tiered by protocol shape
- [`live-test.md`](docs/live-test.md) — running against real hardware
- [`architecture.md`](docs/architecture.md) — component layers and library/app split
- [`stack.md`](docs/stack.md) — implementation stack

## Provenance

Built from static analysis of a shipped APK together with controlled live
traffic from a device owned by the author. The vendor app itself was never
executed and requires a mandatory account login, which this project does not
use and will not require.

## License

Licensed under **GPL-3.0-or-later** — see [`LICENSE`](LICENSE).
