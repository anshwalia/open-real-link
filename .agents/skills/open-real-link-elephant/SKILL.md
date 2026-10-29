---
name: open-real-link-elephant
description: Project context and guardrails for designing open-real-link features with the Elephant-Goldfish Model (EGM). Load before /skill:egm-brainstorm, -draft, -review, -resolve or -drift-report.
---

# open-real-link — EGM project context

This is the **public** implementation of the realme/OPPO TL headset protocol
(`tlcore`, `tltransport`, `tlcli`, `tlapp`). It is self-contained: it holds
protocol facts, mappings and code, and nothing from the reverse-engineering
record.

`AGENTS.md` is binding. This skill is the EGM-specific overlay: it tells the
Elephant what to enforce and what to paste into reviewer prompts.

## Where the authority is

- `docs/command-coverage.md` — what is implemented, and its evidence tag.
- `tests/test_protocol_docs.py` — keeps `docs/protocol.md` symbol-accurate.
- `docs/architecture.md` §9 — the build order.
- `docs/app.md` — the app's verified surface and what is deliberately absent.

Do not design against a number recalled from prose. Counts live in the tests and
the ledger, not in this skill.

## The evidence gate (hard)

A protocol fact is `verified` (on hardware, end to end), `observed` (seen live,
not confirmed) or `inferred` (static analysis only). **Only `verified` facts may
be implemented.** An `observed` or `inferred` fact is documented — what it is,
why it matters, how it would be used, and the obstacle that blocks verification
— and never grows a builder, sender, decoder, `Session` method, CLI command,
backup/restore entry or UI control.

Consequences for a design:

- A design that proposes implementing an `observed`/`inferred` ID is out of
  scope; the correct output is a documentation change plus a named obstacle.
- A feature whose trigger needs the vendor app (an in-app guided flow, an AI
  feature, a toggle only the vendor app sends) can never be host-verified. It is
  documented, never designed for implementation.
- The one allowed exception is a **guard that prevents traffic** — e.g. the
  `send` refusal for the tone / OTA / debug families.

## Publish boundary (hard)

The design, its brief, its review report and any test must contain no:

- APK / DEX / decompiled sources, decompiler or reverse-engineering tool output;
- HCI traces, raw capture logs, probe or experiment scripts;
- vendor binaries or extracted vendor assets;
- device MAC addresses, paired-device names, absolute home paths;
- references to any other repository by name, path, or "analysis repository"
  phrasing.

If a fact needs an artifact to explain it, state the **finding** and keep the
artifact out of this repository. Do not let a reviewer's code search escape this
tree.

## Safety (hard)

Never design an implementation of the hearing-test / ear-scan family (`0x011E`,
`0x011F`, `0x040D`, `0x040E`, `0x0415`); it drives loud test tones. A noise-mode
write (`0x0404 action=1`) is accepted only while the buds are worn.

## Artifact locations

- Scratch (gitignored): `.dev/docs/` — `concepts_brief.md`, `design_draft.md`,
  `goldfish_review_report.md`, `*_DRIFT_REPORT.md`.
- Committed: `docs/designs/` for an approved design only.

## Guardrails to paste into every reviewer prompt

Reviewers run in a fresh context and do not read `AGENTS.md` or this skill. When
you spawn `egm-*` subagents, include this block verbatim in the prompt:

> Repository: `open-real-link`, the public TL headset controller. Enforce:
> (1) implement only `verified` facts; `observed`/`inferred` facts are documented
> with their blocking obstacle, never implemented — do not propose a builder,
> sender, decoder, `Session` method, CLI command or UI control for one;
> (2) the document must contain no reverse-engineering material, MAC address,
> paired-device name, absolute home path, or reference to any other repository;
> (3) never design the hearing / ear-scan family (`0x011E`, `0x011F`, `0x040D`,
> `0x040E`, `0x0415`); (4) `docs/command-coverage.md` is the authority for what
> is implemented; (5) do not cite counts from prose. Stay inside this
> repository. Report a finding, not an implementation.

## Drift checks worth running

`/skill:egm-drift-report docs` compares each document with the code it names.
The pairs that matter most here: `docs/command-coverage.md` ↔
`tlcore/commands.py`, `docs/protocol.md` ↔ the `tlcore` payload / decoder
symbols, `docs/app.md` ↔ `tlapp`, `docs/architecture.md` §9 ↔ the built layers.
