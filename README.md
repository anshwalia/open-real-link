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

The protocol core, `tlcore`, is complete and verified: it produces and parses
the same bytes as a reference implementation that was validated on real
hardware. Platform transports and the command-line front end are not yet
implemented; `tlcore` is currently used as a library.

## Status

`tlcore` is complete and byte-for-byte verified. Platform transports and user
interfaces are still to be built.

Verified on hardware (realme Buds Air7 Pro):

| Capability | Status |
| --- | --- |
| Read active noise mode | Verified |
| Set active noise mode | Verified end to end |
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
them; it cannot work on iOS or in a web browser, which do not expose SPP. No
platform transport is implemented yet; macOS is the intended first target.

## Safety

Some commands recovered during reverse engineering drive **loud, repeated test
tones** in the wearer's ears (the hearing-test and ear-scan family). Their
command IDs are recorded in `tlcore` for identification, but no payload builder
or decoder is provided for them. Do not sweep unknown commands against a headset
that is being worn; any extension of this project should gate writes behind an
explicit opt-in.

## Installation

The package is **not yet published to PyPI**. Install from source:

```console
$ git clone https://github.com/anshwalia/open-real-link.git
$ cd open-real-link
$ pip install .
```

The protocol core has no dependencies and imports no platform-specific module,
so it can be used and tested anywhere, including against recorded captures.

The planned platform transports ship as optional extras, installed the same way:

```console
$ pip install ".[macos]"   # macOS
$ pip install ".[linux]"   # Linux
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

## Project layout

| Path | Contents |
| --- | --- |
| `tlcore/` | Protocol core: framing, packets, commands, payloads, decoders |
| `tltransport/` | Per-platform transports (macOS planned) — not yet implemented |
| `tlcli/` | Command-line front end — not yet implemented |
| `fixtures/` | Recorded device traffic used as golden test input |
| `tests/` | Verification against the reference codec, plus the protocol-docs guard |
| `docs/` | Protocol reference and design notes |

## Documentation

Additional documentation lives in [`docs/`](docs/):

- [`protocol.md`](docs/protocol.md) — the wire format, command set, and which
  commands are implemented and verified
- [`architecture.md`](docs/architecture.md) — component layers and library/app split
- [`stack.md`](docs/stack.md) — implementation stack

## Provenance

Built from static analysis of a shipped APK together with controlled live
traffic from a device owned by the author. The vendor app itself was never
executed and requires a mandatory account login, which this project does not
use and will not require.

## License

Licensed under **GPL-3.0-or-later** — see [`LICENSE`](LICENSE).
