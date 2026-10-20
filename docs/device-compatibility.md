# Device compatibility — the static candidate list

`tlcore` is a protocol library, not a per-model driver. It speaks the TL
protocol family and, at connect time, asks the device which commands it accepts
(see [`protocol.md`](protocol.md) §6). That means the *authoritative* answer to
"does this model work?" always comes from the device itself, over `0x8100`.

This document adds the **static** half: the models whose bundled per-model
configuration says they speak the TL protocol in the same shape as the
live-verified reference model. It is a **candidate** list — a tier list, not a
verified whitelist. Read the limits at the bottom before trusting a row.

## How it was recovered

The reference app ships one configuration file per model. Each file records the
transport and protocol choices for that model. Parsing them yields the table
below. Nothing here is a live result: it is what the app *believes* about each
model, checked for consistency against the recovered command catalogue.

## Signals

| signal | values | meaning |
| --- | --- | --- |
| `uuid` | `default` / `self` | `default` = standard SPP UUID; `self` = a vendor-specific UUID/channel the library does not open yet |
| `noise` | `std` / `free` | how the noise-mode payload is framed; `free` is a different layout |
| `pull` | `std` / `free` | switch-feature protocol variant |
| `interval` | `10` / `50` | milliseconds between commands (50 is the default) |
| `type` | `TWS` / `neck` | form factor |
| `ANC` | `yes` / `—` | whether the model's feature set includes a noise-control group |

## Tiers

The tier is a **heuristic**, computed from the signals above:

- **A** — standard transport, standard protocols, an ANC group, and 10 ms
  pacing: the same shape as the live-verified model. Best odds.
- **B** — TL over the standard transport, but missing a feature group or using
  the default 50 ms pacing. The library still works; fewer commands are enabled
  by the device.
- **C** — a non-standard transport (`self`) or protocol (`free`). The UUID or the
  payload layout may differ, so these need a protocol check before they can be
  claimed.

Tier is about *protocol shape*, not quality or age. A tidy numbering of the rows
is not a ranking.

## Table

`—` in the model-id column means the model ships no id. Note that model ids are
**not unique** (`066452` is shared by the T300 Pro and T500 Pro; `065052` by the
T02 and T200 Lite).

| model | model id | uuid | interval | noise | pull | type | tier | ANC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DIZO Buds Z Power` | `—` | self | 50 | free | free | TWS | **C** | — |
| `realme Buds Air 3S` | `061012` | default | 50 | free | std | TWS | **C** | — |
| `realme Buds Air 5` | `062012` | default | 50 | std | std | TWS | B | yes |
| `realme Buds Air 5 Pro` | `061C12` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Air6` | `063412` | default | 50 | std | std | TWS | B | yes |
| `realme Buds Air6 Pro` | `063012` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Air7` | `064812` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Air7 Pro` | `064C12` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Air8` | `066812` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Air8 Pro` | `066C12` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Clip` | `065C12` | default | 10 | std | std | TWS | B | — |
| `realme Buds N1` | `064012` | default | 50 | std | std | TWS | B | yes |
| `realme Buds N1 Pro` | `063812` | default | 50 | std | std | TWS | B | yes |
| `realme Buds T01` | `064412` | self | 50 | std | std | TWS | **C** | — |
| `realme Buds T02` | `065052` | default | 50 | std | std | TWS | B | — |
| `realme Buds T110` | `062812` | self | 50 | std | std | TWS | **C** | — |
| `realme Buds T200` | `065452` | default | 50 | std | std | TWS | B | yes |
| `realme Buds T200 Lite` | `065052` | default | 50 | std | std | TWS | B | — |
| `realme Buds T200x` | `065852` | default | 50 | std | std | TWS | B | yes |
| `realme Buds T300` | `062412` | default | 50 | std | std | TWS | B | yes |
| `realme Buds T300 Pro` | `066452` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds T310` | `063C12` | default | 50 | std | std | TWS | B | yes |
| `realme Buds T500 Pro` | `066452` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds T500 Pro HP Ed` | `067452` | default | 10 | std | std | TWS | **A** | yes |
| `realme Buds Wireless 3` | `050812` | default | 50 | std | std | neck | B | yes |
| `realme Buds Wireless 3 Neo` | `050C12` | default | 50 | std | std | neck | B | — |
| `realme Buds Wireless 5 ANC` | `051412` | default | 50 | std | std | neck | B | yes |
| `realme Buds Wireless 6 Neo` | `051812` | default | 10 | std | std | neck | B | — |
| `realme TechLife Buds` | `066012` | default | 10 | std | std | TWS | B | — |
| `realme TechLife Buds T200 Lite` | `067C52` | default | 50 | std | std | TWS | B | — |
| `realme TechLife Buds T500` | `067052` | default | 10 | std | std | TWS | **A** | yes |
| `realme TechLife Buds T500 Pro` | `067852` | default | 10 | std | std | TWS | **A** | yes |
| `realme TechLife Wireless Z3` | `051C52` | default | 10 | std | std | neck | B | — |
| `realme TechLife Wireless Z3ANC` | `052052` | default | 50 | std | std | neck | B | yes |

## Using it from `tlcore`

The table is importable as data (no I/O):

```python
import tlcore

spec = tlcore.device_spec("064C12")       # model id, case-insensitive
spec.name          # 'realme Buds Air7 Pro'
spec.tier          # 'A'
spec.cmd_interval  # 10

# model ids are not unique
tlcore.devices_for_model("066452")        # (T300 Pro, T500 Pro)
```

This tells you the *static shape* of a model. It does **not** replace the
runtime capability check:

```python
s = tlcore.Session(transport)
caps = s.negotiate_capabilities()   # 0x0100 -> 0x8100 bitmap
s.supports(tlcore.CMD_SET_NOISE_REDUCTION)   # always-on OR advertised
```

## Verifying a model

To promote a row from "candidate" to "verified", run a live check (with the
repository virtualenv active — see [`../AGENTS.md`](../AGENTS.md)). It is one
command, and it needs a worn headset only for noise writes:

```console
$ python -m tlcli --address <MAC> info     # identity + version
$ python -m tlcli --address <MAC> caps     # does it answer 0x8100?
$ python -m tlcli --address <MAC> backup   # optional: capture its real bitmap
```

A model is verified when the transport opens, `0x8100` is answered, identity
polls decode, and the settings you care about round-trip.

## Limits — what the static table cannot tell you

1. **The runtime capability bitmap.** It is emitted by firmware, not stored in
   the app. The table predicts *shape*; the device reports the *set*.
2. **The framing wrapper.** The mapping from product type to the wire wrapper
   (OPPOv1 vs. the common wrapper) is not recovered. A model can have the right
   signature and still need a different outer frame.
3. **Per-model payload values.** The same command id can carry different value
   encodings. The `free` rows are the concrete risk: the noise payload layout
   differs, so those models are tier C for a reason.
4. **"Advertised" is not "answers".** A bit can be set while the command is
   never answered; the tier does not model that.

When static and live disagree, **live wins**.
