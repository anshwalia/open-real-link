# CONTEXT — start here

`open-real-link` is the **public implementation** of the realme/OPPO "TL"
headset protocol: a pure protocol core (`tlcore`), Bluetooth Classic transports
(`tltransport`), a CLI (`tlcli`) and a desktop app (`tlapp`). It holds findings,
protocol mappings and code — and nothing from any reverse-engineering record.

[`AGENTS.md`](AGENTS.md) is **binding**: it states the evidence gate
(`verified` / `observed` / `inferred`), the "do not add here" list, and the
release checks. Read it first.

## 1. State — read it, don't recall it

Counts and consistency are produced, not remembered. With the repository
virtualenv active:

```console
$ pytest -q
$ python tests/test_protocol_docs.py      # docs/protocol.md vs the tlcore symbols
$ python tests/test_command_registry.py   # the command catalogue vs docs/command-coverage.md
$ python tests/test_app.py                # the Qt-free app controller
$ python tests/test_packaging.py          # bundle inputs
```

[`docs/command-coverage.md`](docs/command-coverage.md) is the authority for what
is implemented. Its **"Implemented but not live-verified"** ledger is the only
place a tracked exception may live; it may only shrink, and every entry must be
live-verified or removed before a release.

## 2. Router — task → section

| task | read |
| --- | --- |
| wire format, framing, packets | [`docs/protocol.md`](docs/protocol.md) §2–§4 |
| payload bytes and builders | [`docs/protocol.md`](docs/protocol.md) §7–§8 |
| which IDs are implemented, and their evidence tag | [`docs/command-coverage.md`](docs/command-coverage.md) |
| component shape, library/app split | [`docs/architecture.md`](docs/architecture.md) |
| why the stack is what it is | [`docs/stack.md`](docs/stack.md) |
| what the app exposes, and what it deliberately does not | [`docs/app.md`](docs/app.md) |
| which models are candidates | [`docs/device-compatibility.md`](docs/device-compatibility.md) |
| live hardware runbook | [`docs/live-test.md`](docs/live-test.md) |
| which codec is streaming, which headset to pick (host-side) | [`docs/app.md`](docs/app.md) §"Which headset"; [`docs/live-test.md`](docs/live-test.md) §7.1 |
| building and signing the bundles | [`docs/packaging.md`](docs/packaging.md) |
| designing a new feature (EGM) | [`.agents/skills/open-real-link-elephant/SKILL.md`](.agents/skills/open-real-link-elephant/SKILL.md) |

## 3. Do not rediscover — settled, with a reason

- **SPP/RFCOMM only.** There is no BLE control path; that rules out iOS and the
  web. [`docs/protocol.md`](docs/protocol.md) §1.
- **Only macOS and Windows have a live transport.** Linux/Android are not
  written; on a platform with none, `--transport auto` raises
  `TransportUnavailable` naming the platform rather than falling back to a
  recording. `replay` works everywhere and is reached only by asking for it.
  [`docs/architecture.md`](docs/architecture.md) §4.
- **Retired commands.** `0x0124` (never answered), `0x0418` (the vendor app
  refuses a zero-band EQ record) and `0x0403` (accepted, a provable no-op) were
  driven on hardware, could not be verified, and were removed rather than
  shipped as guesses. [`docs/command-coverage.md`](docs/command-coverage.md).
- **Vendor-app-only IDs.** `0x0503` and `0x0516` need the official app running,
  so their trigger is not host-reachable and no live test can confirm them.
  Documented, never implemented.
- **The debug channel is refused** by retail units. [`docs/protocol.md`](docs/protocol.md) §7.

## 4. Safety — non-negotiable

- **Never sweep the hearing / ear-scan family** (`0x011E`, `0x011F`, `0x040D`,
  `0x040E`, `0x0415`): it drives loud, repeated test tones in the wearer's ears.
- A noise-mode write (`0x0404 action=1`) is accepted only while the buds are
  **worn**; otherwise the device answers `0x8404 0e`.
- Never write unknown commands to a worn headset.
- **After every live session, restore the host:** `python3
  tools/host_audio_check.py --fix`. Repeated ACL reconnects re-sync the Mac's
  output volume from the headset, and a headset reporting 0 leaves the Mac
  silent while the audio route still looks healthy. The control is **shared**
  (Mac and buds are one absolute volume, last writer wins), so only 0 or muted
  is a fault ([`docs/live-test.md`](docs/live-test.md) §7).

## 5. Token hazards

| file | do instead |
| --- | --- |
| `tlapp/window.py` | `grep` for the widget or handler, then read that block |
| `tlcli/cli.py` | `grep` for the subcommand |
| `docs/live-test.md` | read the section you need, not the whole log |

## 6. Full orientation

`AGENTS.md` (rules and where things stand) → [`docs/architecture.md`](docs/architecture.md)
§9 (build order) → [`docs/app.md`](docs/app.md) (the app surface and what is
deliberately absent). For design work, read
[`.agents/skills/open-real-link-elephant/SKILL.md`](.agents/skills/open-real-link-elephant/SKILL.md)
before any `/skill:egm-*` command.
