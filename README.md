# OpenRealLink

App-free control of **realme / OPPO "TL" Bluetooth headsets** — the protocol
recovered by reverse engineering the realme Link Android app.

> **Not affiliated with, endorsed by, or supported by realme or OPPO.** "realme",
> "OPPO" and "realme Link" are trademarks of their respective owners and are used
> here only to say what this software interoperates with. No vendor code, binary
> or asset is redistributed.

## Naming

| | |
|---|---|
| distribution | **`open-real-link`** |
| import | **`tlcore`** |

They differ on purpose. The distribution name is the public, searchable identity
and must not present a vendor trademark as its own; the import name is a short,
stable namespace that sits next to the vendor's own `com.realme.iot.headset.tl`
package, so a line in the port can be traced back to the class it came from
without a lookup table. Sibling layers follow the same prefix: `tltransport`,
`tlcli`.

See [`docs/architecture.md`](docs/architecture.md) §8 for why `tlv`, `tls`, `tl`
and the other obvious candidates were rejected.

`TL` is never expanded in the vendor's code, so it is not expanded here either.

## Status

**Early.** The protocol core is ported and verified; the transport and the CLI
are next. What is proven today, on hardware:

| | |
|---|---|
| read noise mode | ✅ |
| **write** noise mode | ✅ — verified end to end on a Buds Air7 Pro |
| read battery / wear / firmware / model / use-time | ✅ |
| capability bitmap → supported-command set | ✅ |
| encode/decode parity with the validated reference codec | ✅ 23,160 assertions |

The six-value noise vocabulary, confirmed **by ear** on the target device:

| value | mode |
|---|---|
| `0x01` | Normal (noise off) |
| `0x02` | Transparency |
| `0x04` | ANC light |
| `0x08` | ANC deep |
| `0x10` | ANC medium |
| `0x20` | ANC smart (adaptive) |

> A mode write is **accepted only while the buds are worn** (in-ear). Otherwise
> the device answers `0x8404 0e`. Read-back is authoritative; the asynchronous
> `0x0204` push is racy and is treated as a hint only.

## Scope

The Buds Air7 Pro speaks **Bluetooth Classic RFCOMM/SPP only** — there is no
BLE/GATT control key for this model, and that was established by experiment, not
assumption. So this works on **macOS, Windows, Linux and Android**, and cannot
work on iOS or in a browser.

## Safety

Some recovered commands drive **loud, repeated test tones** in the wearer's ears
— the hearing-test and ear-scan family. Those are documented and deliberately
**not** implemented. Do not blind-sweep command space against a headset somebody
is wearing; if you extend this, gate writes behind an explicit opt-in.

## Quick start

```console
$ pip install open-real-link
$ python -c "import tlcore; print(tlcore.NOISE_MODE_NAMES)"
{1: 'Normal (noise off)', 2: 'Transparency', 4: 'ANC light', 8: 'ANC deep', 16: 'ANC medium', 32: 'ANC smart'}
```

`tlcore` is pure protocol: it has **no dependencies** and imports no platform
module, so it can be exercised against recorded captures anywhere.

## Layout

| path | what |
|---|---|
| `tlcore/` | protocol core — framing, packets, commands, payloads, decoders |
| `tltransport/` | per-platform byte movers (macOS IOBluetooth first) |
| `tlcli/` | command-line front end |
| `fixtures/` | recorded device traffic, used as golden test input |
| `tests/` | parity harness against the validated reference codec |
| `docs/` | architecture and stack decisions |

## Provenance

Built by **static** analysis of a shipped APK plus controlled live traffic from a
device the author owns. The vendor app itself was never executed, and it requires
a mandatory account login, which this project does not and will not have.

Licensed **GPL-3.0-or-later** — see [`LICENSE`](LICENSE).
