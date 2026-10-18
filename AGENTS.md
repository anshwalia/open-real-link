# AGENTS.md — working rules for this repository

`open-real-link` is the **public implementation** of the realme/OPPO "TL"
headset protocol. It contains exactly three kinds of thing, and nothing else:

1. **Findings** — protocol facts, each tagged `verified` / `observed` /
   `inferred`.
2. **Protocol mappings** — wire layouts, command IDs, capability groups and
   value tables (`docs/protocol.md`, `docs/command-coverage.md`, `tlcore/`).
3. **Implementation** — `tlcore`, `tltransport`, `tlcli`, tests and fixtures.

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

## Conventions

- `tlcore` is **pure**: no platform imports and no I/O. Transports live in
  `tltransport`; the UI/CLI in `tlcli`.
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

- `python3 -m pytest -q` passes.
- `python3 tests/test_protocol_docs.py` prints `DOCS: OK`.
- `TL_REFERENCE=… python3 tests/test_parity_tl_frame.py` prints `PARITY: OK`
  (or skips cleanly when the reference is unavailable).
- Nothing from the "Do not add here" list is staged.
