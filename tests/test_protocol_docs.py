#!/usr/bin/env python3
"""Keep ``docs/protocol.md`` in step with what ``tlcore`` actually implements.

Prose rots. This harness makes two guarantees:

1. **Nothing implemented is undocumented.** Every payload builder (``payload_*``)
   and decoder (``parse_*``) exported by ``tlcore`` must be named in the
   command-surface block of ``docs/protocol.md``.
2. **Nothing documented is imaginary.** Every ``tlcore`` symbol referenced in the
   document must exist on the package, so a rename or removal fails loudly here
   instead of misleading a reader.

It is deliberately narrow: it checks *presence and existence*, not the accuracy
of the prose, which no test can judge. Run it directly, or under pytest.

    python3 tests/test_protocol_docs.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOC = ROOT / "docs" / "protocol.md"
BLOCK_START = "<!-- command-surface:start -->"
BLOCK_END = "<!-- command-surface:end -->"

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def documented_symbols(text: str) -> set[str]:
    """All `payload_*` / `parse_*` identifiers named in the command-surface block."""
    start = text.find(BLOCK_START)
    end = text.find(BLOCK_END)
    if start < 0 or end < 0 or end < start:
        return set()
    block = text[start:end]
    return set(re.findall(r"`((?:payload_|parse_)[A-Za-z0-9_]+)`", block))


# Wire-layer parsers share the ``parse_`` prefix but are not command decoders,
# so they are not required to appear in the command-surface table.
WIRE_FUNCTIONS = {"parse_packet", "parse_frame"}


def core_api() -> set[str]:
    """Command payload builders and decoders exported by ``tlcore``."""
    import tlcore

    return {
        name for name in tlcore.__all__
        if name.startswith(("payload_", "parse_")) and name not in WIRE_FUNCTIONS
    }


def main() -> int:
    if not DOC.exists():
        print(f"missing {DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")

    import tlcore

    # 1. the block exists and is populated
    start, end = text.find(BLOCK_START), text.find(BLOCK_END)
    check("command-surface block present", start >= 0 and end > start, True)

    # 2. every implemented builder/decoder is documented
    api = core_api()
    documented = documented_symbols(text)
    check("command-surface block names symbols", len(documented) > 0, True)
    for name in sorted(api - documented):
        FAILURES.append(f"implemented but undocumented in protocol.md: {name}")

    # 3. every documented builder/decoder exists on tlcore
    for name in sorted(documented):
        check(f"documented symbol exists: {name}", hasattr(tlcore, name), True)

    # 4. every `tlcore.<name>` reference in the whole document resolves
    for name in sorted(set(re.findall(r"tlcore\.([A-Za-z_][A-Za-z0-9_]*)", text))):
        check(f"referenced symbol exists: tlcore.{name}", hasattr(tlcore, name), True)

    # 5. the noise table matches the library, value for value
    for value, label in tlcore.NOISE_MODE_NAMES.items():
        check(f"noise mode 0x{value:02X} documented", label in text, True)

    # 6. the worked capability example is still the real answer
    caps = tlcore.parse_capabilities(bytes.fromhex("00bf4f5ae665dc4004"))
    check("capability example hex documented", "00bf4f5ae665dc4004" in text, True)
    check("capability example command count documented",
          f"(True, {len(caps['commands'])})" in text, True)

    if FAILURES:
        print(f"checks run: {CHECKS}")
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"checks run: {CHECKS}")
    print("DOCS: OK -- protocol.md matches the tlcore surface")
    return 0


def test_protocol_docs() -> None:
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
