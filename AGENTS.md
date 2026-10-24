# AGENTS.md — working rules for this repository

`open-real-link` is the **public implementation** of the realme/OPPO "TL"
headset protocol. It contains exactly three kinds of thing, and nothing else:

1. **Findings** — protocol facts, each tagged `verified` / `observed` /
   `inferred`.
2. **Protocol mappings** — wire layouts, command IDs, capability groups and
   value tables (`docs/protocol.md`, `docs/command-coverage.md`, `tlcore/`).
3. **Implementation** — `tlcore`, `tltransport`, `tlcli`, `tlapp`, tests and
   fixtures.

## Do not add here

This repository is deliberately free of reverse-engineering material. Do **not**
commit, and do not paste into a doc or test:

- APK / DEX files, decompiled sources, or excerpts from them;
- REA / jadx / androguard output, or reverse-engineering tool logs;
- HCI traces (`.pklg`, `btsnoop`), raw capture logs, or probe/experiment scripts;
- vendor binaries or extracted vendor assets;
- device MAC addresses, paired-device names, or absolute home paths;
- references to any other repository by name or path.

If a protocol fact needs a raw capture or a decompiled excerpt to explain it,
state the **finding** here and keep the artifact elsewhere.

## Where things stand

The protocol work is done; what remains is a UI and two transport shims.

| layer | state | read |
| --- | --- | --- |
| `tlcore` | done, parity-verified | [`protocol.md`](docs/protocol.md), [`command-coverage.md`](docs/command-coverage.md) |
| `tltransport` | macOS live transport + portable replay done; Linux/Windows/Android still to write — until then `--transport auto` off macOS raises `TransportUnavailable` naming the platform rather than falling back to replay | [`architecture.md`](docs/architecture.md) §3, §4 |
| `tlcli` | done (the hardware test harness) | [`live-test.md`](docs/live-test.md) |
| `tlapp` | **in progress** — connect, status, noise mode, EQ preset + list, high volume, find my buds, paired-device list, gesture table, backup/restore, event monitor, a connect-time compatibility check, and two views: General and Expert (diagnostics, bitmap, bass read, guarded raw console) | [`app.md`](docs/app.md) |

The app is built **one verified feature at a time**; `docs/app.md` lists what is
in it and what is deliberately not yet. The build order is in
[`architecture.md`](docs/architecture.md) §9.

## Implement only what is verified and live-tested

**Do not implement a protocol fact that has not been verified on hardware and
live-tested end to end.** "Implement" means anything that makes the project
*do* the thing: a payload builder, a sender, a decoder, a `Session` method, a
CLI command, a backup/restore entry, or a UI control.

The evidence tag is the gate:

| tag | meaning | allowed here |
| --- | --- | --- |
| `verified` | confirmed on hardware, end to end | implement **and** document |
| `observed` | seen live, not fully confirmed | document only |
| `inferred` | derived by static analysis, never run | document only |

An `observed` or `inferred` fact is still worth recording — as **documentation**:
*what* it is, *why* it matters, *how* it would be used, and *what blocks
verification* (the obstacle). Name the obstacle explicitly (device refuses it,
no read-back path, needs a worn headset, unsafe to drive, layout unconfirmed),
and say it is not implemented. A documented gap is fine; a silent guess is not.

One narrow exception: a guard whose only job is to **prevent** traffic — the
`send` refusal for the tone / OTA / debug families — is not an implementation of
the blocked feature, and is allowed even though the feature is not.

**Tracked exceptions.** None are open. A handful of symbols were implemented
before this rule existed; each was taken to live hardware, none could be
verified, and all three were **removed** rather than shipped as guesses. The
ledger in [`docs/command-coverage.md`](docs/command-coverage.md) records the
three and why each failed, and is **empty**. Do not add a new exception — the
ledger may only shrink, and it has nowhere left to shrink from. Resolve any
future one as described under "Before releasing".

## Dev environment

Use the virtualenv at the **repository root**. Do not install the project or its
dependencies into the system or user site.

```console
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e ".[dev,macos,gui]"
$ pytest -q
```

`.[macos]` is the IOBluetooth transport, `.[gui]` is PySide6 for the app, and
`.[dev]` is the test/lint tools. Every command in this repository assumes that
virtualenv is **active** — `.venv/bin/python` and `.venv/bin/pip` are the exact
equivalents if you would rather not activate. Nothing here is installed
globally.

## Conventions

- `tlcore` is **pure**: no platform imports and no I/O. Transports live in
  `tltransport`; the CLI in `tlcli`; the GUI in `tlapp` (whose controller is
  itself Qt-free, so `import tlapp` never pulls in PySide6).
- The codec is verified byte-for-byte against an external reference through
  `tests/test_parity_tl_frame.py`, which reads `TL_REFERENCE` from the
  environment and **skips cleanly** when unset. Never hard-code a reference path.
- `docs/protocol.md` is kept in step with `tlcore` by
  `tests/test_protocol_docs.py`: every exported `payload_*` / `parse_*` symbol
  must appear in its command-surface block.
- Tag facts honestly: `verified` (confirmed on hardware), `observed` (seen live,
  not fully confirmed), `inferred` (derived, not live). `docs/command-coverage.md`
  is the authority for what is implemented.
- `README.md` claims must match the code; the project is **not** on PyPI, so
  document source installs only.

## Safety

- Never sweep the hearing-test / ear-scan family (`0x011E`, `0x011F`, `0x040E`,
  `0x0415`) — those commands drive loud test tones.
- A noise-mode write (`0x0404 action=1`) is accepted only while the buds are
  **worn**; otherwise the device answers `0x8404 0e`.

## Before committing

- `pytest -q` passes.
- `python tests/test_app.py` prints `APP: OK` (the Qt-free app controller).
- `python tests/test_protocol_docs.py` prints `DOCS: OK`.
- `TL_REFERENCE=… python tests/test_parity_tl_frame.py` prints `PARITY: OK`
  (or skips cleanly when the reference is unavailable).
- Nothing from the "Do not add here" list is staged.
- `python tests/test_app.py` covers the Qt-free controller, and
  `tests/test_parity_tl_frame.py::test_core_purity` is the check that keeps
  `tlcore` platform-free. It asserts, deliberately: a test built only from the
  harness's `check()` helper records failures and returns, so it can never turn
  red under pytest. If you add a rule of this kind, break it once on purpose and
  watch the test fail before you trust it.

## Before releasing

- Every entry in the `docs/command-coverage.md` "Implemented but not
  live-verified" ledger has been **either live-verified (and struck from the
  ledger) or removed from the code**. Nothing ships with a ledger entry open.
