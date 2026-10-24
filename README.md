# OpenRealLink

**Control your realme / OPPO Bluetooth earbuds from your own computer — no
phone, no vendor app, no account.**

`open-real-link` talks to TL-series headsets (the ones the realme Link app
manages) directly over Bluetooth Classic, using the same channel the official
app uses. The protocol was recovered by reverse engineering the realme Link
Android app, and the recovered core is verified byte-for-byte against a
reference implementation that was validated on real hardware.

> **Not affiliated with, endorsed by, or supported by realme or OPPO.**
> "realme", "OPPO" and "realme Link" are trademarks of their respective owners
> and are used here only to describe what this software interoperates with. No
> vendor code, binary, or asset is redistributed.

---

## What is this?

Most earbud settings — noise cancelling, the equalizer, the loudness boost,
what each tap does — live behind the vendor's phone app. This project replaces
that app with an open protocol implementation you can drive from a laptop:

- a **pure protocol library** (`tlcore`) that builds and parses the headset's
  own packets, with no I/O and no platform dependencies;
- **transports** that carry those packets over Bluetooth Classic RFCOMM/SPP;
- a **command-line tool** (`tlcli`) and a **desktop app** (`tlapp`) built on top.

Because the library is separated from the UI, the recovered protocol is reusable
on its own — in a script, in another language's port, or in a future app — and
the UI can be rewritten without touching it.

## What it can do

Everything below was confirmed on a real **realme Buds Air7 Pro**, end to end:

| Capability | Status |
| --- | --- |
| Read the active noise mode | Verified |
| Set the active noise mode | Verified end to end |
| Read / write the sound-effect (EQ) mode | Verified end to end |
| Read / write high volume (loudness boost) | Verified end to end |
| Read battery, wear state, firmware, model, and use time | Verified |
| Read the capability bitmap → supported-command set | Verified |
| Read the multi-band bass engine (`0x012C`) | Verified end to end |
| Write the bass-engine value (both `0x041B` forms) | Verified end to end |
| Byte-for-byte parity with the reference codec | Verified (23,247 checks) |

The noise modes, confirmed by listening on the target device:

| Value | Mode |
| --- | --- |
| `0x01` | Normal (noise off) |
| `0x02` | Transparency |
| `0x04` | ANC light |
| `0x08` | ANC deep |
| `0x10` | ANC medium |
| `0x20` | ANC smart (adaptive) |

On top of these reads and writes, the command-line tool and the desktop app also
back up and restore the settings a device advertises, list paired devices, read
the gesture table, ring the buds to find them, and show the device's own push
events live.

> **A mode change is accepted only while the earbuds are worn.** Otherwise the
> device rejects it. Always read the mode back to confirm the change — the
> asynchronous status push can arrive out of order and should be treated as a
> hint, not the source of truth.

### Deliberately not implemented

A few command IDs are missing on purpose, because a live Air7 Pro showed they
cannot be verified — not because they were hard:

- `0x0418` — the vendor app refuses to send a zero-band EQ record, and the band
  table comes from its editor;
- `0x0403` — accepted by the device, but a provable no-op;
- `0x0124` — never answered; the bass value comes from `0x012C` instead.

They are documented in [`docs/protocol.md`](docs/protocol.md) §7 and
[`docs/command-coverage.md`](docs/command-coverage.md), and no builder ships for
them.

## Is my headset supported?

The protocol runs over **Bluetooth Classic RFCOMM/SPP**. That channel is
available on macOS, Windows, Linux, and Android, so the protocol can be driven
on any of them — it **cannot** work on iOS or in a web browser, which do not
expose SPP.

Only the **macOS** transport is implemented today; the Linux, Windows, and
Android transports are still to be written. Until then `--transport auto` (the
default) **refuses to guess**: off macOS it raises `TransportUnavailable`, naming
the platform and suggesting `--replay FILE`, rather than quietly opening a
recording.

This is a reverse-engineered project, so support is empirical rather than
promised. The verified model is the Buds Air7 Pro; the static candidate list in
[`docs/device-compatibility.md`](docs/device-compatibility.md) names other TL
models by protocol shape, but only the Air7 Pro has been driven on hardware.

## Safety

Three recovered command families are **documented but not implemented** — no
builder, decoder, or CLI command is provided for any of them. Their IDs are
recorded only so their traffic can be identified and refused:

- the **hearing-test / ear-scan** family (`0x011E`, `0x011F`, `0x040E`, `0x0415`)
  drives **loud, repeated test tones** in the wearer's ears;
- the **OTA** family (`0x0300`–`0x0308`) is a firmware **push** state machine
  with **no read-back** path, and it is ungated, so a stray write could start a
  flash;
- the **debug** family (`0x0F00`–`0x0F02`) toggles real firmware verbosity and is
  refused by retail units.

Two **vendor-app-only** requests (`0x0503`, `0x0516`) are a separate case: they
are unverifiable rather than unsafe, because their trigger is the official app
running an AI or feature-sync flow, which this project cannot reach. They are
documented and deliberately left undecoded.

**If you extend this project, gate writes behind an explicit opt-in, and never
sweep unknown commands against a headset that is being worn.** See
[`docs/protocol.md`](docs/protocol.md) §7 for the recorded shapes and the
non-implementation rationale.

## Install

### Requirements

- **Python 3.9 or newer**
- **git**, to get the source
- A **Bluetooth Classic** radio on the machine doing the talking. On macOS the
  transport uses `IOBluetooth`; no extra driver is needed.

### Set up a checkout

The command-line tool and the desktop app are run from a checkout. Clone the
repository and install into a virtualenv at its root:

```console
$ git clone https://github.com/anshwalia/open-real-link.git
$ cd open-real-link
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e ".[macos,gui]"
```

The extras are optional and independent:

| Extra | Adds |
| --- | --- |
| `macos` | the IOBluetooth transport (`pyobjc-framework-IOBluetooth`) |
| `gui` | PySide6, for the desktop app |
| `dev` | the test and lint tools (`pytest`, `coverage`, `ruff`) |
| `release` | PyInstaller, for building the macOS app bundle |

Add `dev` if you intend to run the test suite: `pip install -e ".[dev,macos,gui]"`.

### Just the library

If you only want the protocol core to build on in your own project, the package
installs on its own and has **no dependencies**:

```console
$ pip install .
```

The distribution is **not yet published to PyPI**, so install from source. Note
that the wheel currently ships only the `tlcore` library; `tlcli`, `tlapp`, and
the transports come with a checkout.

### Build a macOS app bundle

A frozen `OpenRealLink.app` — no Python or pip required on the target Mac — is
built from a checkout with PyInstaller:

```console
$ release/macos/build.sh            # writes dist/macos/OpenRealLink.app
$ MAKE_DMG=1 release/macos/build.sh # ...and a .dmg around it
```

The bundle is ad-hoc signed, so it runs on the machine that built it; a public
download needs a Developer ID signature and notarisation. See
[`docs/packaging.md`](docs/packaging.md).

## Try it without a headset

Every part of the tool runs against a recorded capture, so you can explore the
whole thing with **no device and no radio**. Use the fixture bundled in the
repository:

```console
$ python -m tlcli --replay fixtures/spp-smoketest.jsonl info
$ python -m tlapp --replay fixtures/spp-smoketest.jsonl
```

The replay fixtures carry no device address, so they are safe to use and share
anywhere.

## Use it

### Command line

Run from the activated checkout. Replace `AA-BB-CC-DD-EE-FF` with your headset's
address:

```console
$ python -m tlcli --address AA-BB-CC-DD-EE-FF smoke       # read-only live check
$ python -m tlcli --address AA-BB-CC-DD-EE-FF info        # model, firmware, battery, wear, mode
$ python -m tlcli --address AA-BB-CC-DD-EE-FF mode set anc
$ python -m tlcli --address AA-BB-CC-DD-EE-FF eq          # read the EQ mode + preset list
$ python -m tlcli --address AA-BB-CC-DD-EE-FF high-volume # read the loudness boost
$ python -m tlcli --address AA-BB-CC-DD-EE-FF events --seconds 15   # decode pushes
$ python -m tlcli --address AA-BB-CC-DD-EE-FF backup settings.json
$ python -m tlcli --address AA-BB-CC-DD-EE-FF restore --dry-run settings.json
```

`python -m tlcli --help` lists every subcommand. For a hardware runbook and
troubleshooting, see [`docs/live-test.md`](docs/live-test.md).

### Desktop app

```console
$ python -m tlapp                                     # connect by hand
$ python -m tlapp --address AA-BB-CC-DD-EE-FF --connect
$ python -m tlapp --replay fixtures/spp-smoketest.jsonl
```

The app has a **General** view for everyday use — status, noise mode, equalizer,
high volume, gestures, find-my-buds, backup/restore, and a live event monitor —
and an **Expert** view for the diagnostic reads, the capability bitmap, the
bass-engine read, and a guarded raw console that refuses the tone / OTA / debug
families. On connect it runs a compatibility check against the unit's own
capability bitmap and reports which everyday features it advertises. See
[`docs/app.md`](docs/app.md).

### As a library

```python
import tlcore

packet = tlcore.build_packet(
    tlcore.CMD_SET_NOISE_REDUCTION,
    tlcore.payload_set_noise_mode(tlcore.NOISE_MODE_ANC),
)
for frame in tlcore.build_frames(packet, max_frame_size=512):
    ...  # write each frame to your transport, ~10 ms apart for the Air7 Pro
```

## Project layout

| Path | Contents |
| --- | --- |
| `tlcore/` | Protocol core: framing, packets, commands, payloads, decoders, backup/restore. Pure, no I/O |
| `tltransport/` | `MacOSTransport` (IOBluetooth RFCOMM) and `ReplayTransport` (offline) |
| `tlcli/` | Command-line front end, `python -m tlcli` |
| `tlapp/` | PySide6 desktop app, `python -m tlapp` (Qt-free controller + widgets) |
| `fixtures/` | Recorded device traffic used as golden test input |
| `tests/` | Reference-codec parity, transport/session, macOS transport, and docs guards |
| `docs/` | Protocol reference and design notes |
| `release/macos/` | PyInstaller spec, entry point, icon and build scripts for the `.app` |

## Documentation

- [`docs/protocol.md`](docs/protocol.md) — the wire format, command set, and
  which commands are implemented and verified
- [`docs/command-coverage.md`](docs/command-coverage.md) — every known TL command
  ID, with what this project implements and what an Air7 Pro accepts
- [`docs/device-compatibility.md`](docs/device-compatibility.md) — the static
  candidate list of models, tiered by protocol shape
- [`docs/live-test.md`](docs/live-test.md) — running against real hardware
- [`docs/app.md`](docs/app.md) — the desktop app: what it exposes, how it is
  tested, and its safety model
- [`docs/architecture.md`](docs/architecture.md) — component layers and the
  library/app split
- [`docs/stack.md`](docs/stack.md) — the implementation stack
- [`docs/packaging.md`](docs/packaging.md) — building and signing the macOS app
  bundle, and the plan for Windows and Linux

## Provenance

Built from static analysis of a shipped APK together with controlled live
traffic from a device owned by the author. The vendor app itself was never
executed and requires a mandatory account login, which this project does not use
and will not require.

## License

Licensed under **GPL-3.0-or-later** — see [`LICENSE`](LICENSE).
