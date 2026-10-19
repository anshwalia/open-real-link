#!/usr/bin/env python3
"""Keep the full recovered command catalogue from rotting.

``tlcore.ALL_COMMANDS`` is the complete list of request IDs the TL protocol
defines -- all 79 of them, implemented or not.  It is easy for a hand-maintained
table to drift silently: a capability group edited, an ID renamed, a name
dropped.  This harness pins it three ways:

* the size and the families are fixed (79 request IDs, six manager families);
* the gating recorded for every ID agrees exactly with where it lives --
  ``always-on`` iff it is in ``ALWAYS_ON``, ``cap-bit N`` iff it is in
  ``CAPABILITY_TABLE[N]``, ``ungated`` otherwise;
* every curated ``CMD_NAMES`` entry is present and its name matches.

It needs no external reference, so it runs as part of the ordinary suite.  The
byte-level cross-check against the reference codec is
``tests/test_parity_tl_frame.py`` (``ALL_COMMANDS`` there too).

Run it directly, or under pytest.

    python3 tests/test_command_registry.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore  # noqa: E402

#: manager-class mask -> the family label used in the registry
FAMILY_BY_CLASS = {
    tlcore.CLASS_POLL: "poll",
    tlcore.CLASS_NOTIFY: "notify",
    tlcore.CLASS_UPGRADE: "OTA",
    tlcore.CLASS_SET: "set",
    tlcore.CLASS_REQUEST: "device->app",
    tlcore.CLASS_DEBUG: "debug",
}

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def main() -> int:
    specs = tlcore.ALL_COMMANDS
    by_id = {spec.cmd: spec for spec in specs}

    # 1. size, uniqueness, and the derived membership set
    check("command count", len(specs), 79)
    check("unique ids", len(by_id), len(specs))
    check("COMMAND_IDS", tlcore.COMMAND_IDS, frozenset(by_id))

    # 2. every ID belongs to a known family and the mask agrees
    for spec in specs:
        check(f"0x{spec.cmd:04X} family",
              FAMILY_BY_CLASS.get(tlcore.cmd_class(spec.cmd)), spec.family)

    # 3. gating is exactly determined by the capability tables
    gated: dict[int, str] = {}
    for bit, group in enumerate(tlcore.CAPABILITY_TABLE):
        for cmd in group:
            gated[cmd] = f"cap-bit {bit}"
    for spec in specs:
        if spec.cmd in tlcore.ALWAYS_ON:
            check(f"0x{spec.cmd:04X} gating", spec.gating, "always-on")
        elif spec.cmd in gated:
            check(f"0x{spec.cmd:04X} gating", spec.gating, gated[spec.cmd])
        else:
            check(f"0x{spec.cmd:04X} gating", spec.gating, "ungated")

    # ... and nothing gated is missing from the registry.  An ID can be both
    # always-on and a member of a capability group (0x0F00 is in Protocol.c2
    # *and* in b2[23]); always-on takes precedence, so the reverse check must
    # not expect a cap-bit gating for an always-on ID.
    for cmd, gating in gated.items():
        check(f"0x{cmd:04X} in registry", cmd in by_id, True)
        if cmd in by_id:
            want = "always-on" if cmd in tlcore.ALWAYS_ON else gating
            check(f"0x{cmd:04X} gating (reverse)", by_id[cmd].gating, want)
    for cmd in tlcore.ALWAYS_ON:
        check(f"0x{cmd:04X} in registry", cmd in by_id, True)

    # 4. curated names are a subset and agree where both exist
    for cmd, name in tlcore.CMD_NAMES.items():
        check(f"0x{cmd:04X} in registry", cmd in by_id, True)
        if cmd in by_id:
            check(f"0x{cmd:04X} name", by_id[cmd].name, name)

    # 5. lookup helper
    check("command_spec known", tlcore.command_spec(0x0404).cmd, 0x0404)
    check("command_spec unknown", tlcore.command_spec(0x1234), None)

    if FAILURES:
        print(f"checks run: {CHECKS}")
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"checks run: {CHECKS}")
    print("COMMAND REGISTRY: OK -- 79 commands, gating matches the capability tables")
    return 0


def test_command_registry() -> None:
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
