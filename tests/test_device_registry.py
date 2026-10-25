#!/usr/bin/env python3
"""Keep the static device-compatibility table honest.

``tlcore.KNOWN_DEVICES`` is a **candidate** list: the models whose bundled
per-model configuration says they speak the TL protocol family in the same shape
as the live-verified reference model.  It is not a whitelist -- the commands a
given unit accepts are negotiated at runtime via ``0x8100``.

The table is easy to corrupt by hand, so this harness pins it:

* size (34), unique names, and the tier distribution;
* every signal uses a value the protocol layer understands;
* the recorded ``tier`` is exactly what the documented rule computes from the
  signals, so the classification can't drift from the data;
* the model-id index agrees with the table (including the known duplicate ids);
* the lookup helpers work, and the live-verified model is present and tier A.

It needs no external reference, so it runs as part of the ordinary suite.

    python3 tests/test_device_registry.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tlcore  # noqa: E402

ALLOWED_UUID = {"TYPE_DEFAULT", "TYPE_SELF_PROTOCOL"}
ALLOWED_PROTOCOL = {"PROTOCOL_STANDARD", "PROTOCOL_FREE_STYLE"}
ALLOWED_DEVICE_TYPE = {"TYPE_TWS", "TYPE_NECK"}
ALLOWED_TIER = {"A", "B", "C"}

NOISE_GROUP = "GROUP_NOISE_CONTROL"

#: the model that was verified on hardware (docs/command-coverage.md)
VERIFIED_MODEL = ("realme Buds Air7 Pro", "064C12")

MODEL_RE = re.compile(r"^[0-9A-F]{6}$")

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILURES.append(f"{label}\n       got  {got!r}\n       want {want!r}")


def expected_tier(spec: tlcore.DeviceSpec) -> str:
    """The documented classification rule, restated from the signals."""
    if (spec.uuid_type != "TYPE_DEFAULT"
            or spec.noise_protocol != "PROTOCOL_STANDARD"
            or spec.pull_protocol != "PROTOCOL_STANDARD"):
        return "C"
    if NOISE_GROUP in spec.feature_groups and spec.cmd_interval == 10:
        return "A"
    return "B"


def main() -> int:
    devices = tlcore.KNOWN_DEVICES

    # 1. size and uniqueness
    check("device count", len(devices), 34)
    check("unique names", len({d.name for d in devices}), len(devices))

    # 2. signal domains, tiers, and the classification rule
    tier_counts = {"A": 0, "B": 0, "C": 0}
    for d in devices:
        name = d.name
        check(f"{name} uuid", d.uuid_type in ALLOWED_UUID, True)
        check(f"{name} noise", d.noise_protocol in ALLOWED_PROTOCOL, True)
        check(f"{name} pull", d.pull_protocol in ALLOWED_PROTOCOL, True)
        check(f"{name} device_type", d.device_type in ALLOWED_DEVICE_TYPE, True)
        check(f"{name} tier domain", d.tier in ALLOWED_TIER, True)
        check(f"{name} interval", d.cmd_interval in (10, 50), True)
        check(f"{name} model id", d.model_id == "" or bool(MODEL_RE.match(d.model_id)), True)
        check(f"{name} groups sorted/unique",
              list(d.feature_groups), sorted(set(d.feature_groups)))
        check(f"{name} tier", expected_tier(d), d.tier)
        tier_counts[d.tier] += 1

    # 3. the mix is stable; a re-classification should be a deliberate change
    check("tier A count", tier_counts["A"], 11)
    check("tier B count", tier_counts["B"], 19)
    check("tier C count", tier_counts["C"], 4)

    # 4. tier A is exactly the "same shape as the verified model" set
    for d in devices:
        if d.tier == "A":
            check(f"{label(d)} A is standard",
                  (d.uuid_type, d.noise_protocol, d.pull_protocol, d.cmd_interval),
                  ("TYPE_DEFAULT", "PROTOCOL_STANDARD", "PROTOCOL_STANDARD", 10))
            check(f"{label(d)} A has ANC", NOISE_GROUP in d.feature_groups, True)

    # 5. the model-id index agrees with the table
    expected_ids: dict[str, list[str]] = {}
    for d in devices:
        if d.model_id:
            expected_ids.setdefault(d.model_id, []).append(d.name)
    check("index keys", set(tlcore.MODEL_IDS), set(expected_ids))
    for model, names in expected_ids.items():
        check(f"{model} index", [d.name for d in tlcore.devices_for_model(model)], names)

    # known non-unique ids, kept explicit so a change is visible
    check("066452 dupes", sorted(expected_ids["066452"]),
          ["realme Buds T300 Pro", "realme Buds T500 Pro"])
    check("065052 dupes", sorted(expected_ids["065052"]),
          ["realme Buds T02", "realme Buds T200 Lite"])

    # 6. lookup helpers
    check("device_spec known (exact)", tlcore.device_spec("064C12").name, VERIFIED_MODEL[0])
    check("device_spec known (lower)", tlcore.device_spec("064c12").name, VERIFIED_MODEL[0])
    check("device_spec unknown", tlcore.device_spec("FFFFFF"), None)
    check("devices_for_model unknown", tlcore.devices_for_model("FFFFFF"), ())

    # 7. the live-verified model is present and classified as the reference
    ref = tlcore.device_spec(VERIFIED_MODEL[1])
    check("verified model present", ref is not None and ref.name == VERIFIED_MODEL[0], True)
    if ref is not None:
        check("verified model tier", ref.tier, "A")
        check("verified model ANC", NOISE_GROUP in ref.feature_groups, True)

    # 8. the prose table stays in step with the library
    doc = ROOT / "docs" / "device-compatibility.md"
    check("device doc exists", doc.exists(), True)
    if doc.exists():
        text = doc.read_text(encoding="utf-8")
        for d in devices:
            check(f"doc names {d.name}", d.name in text, True)
        for t in ALLOWED_TIER:
            check(f"doc explains tier {t}", f"**{t}**" in text, True)
        section = text.split("## Table", 1)[1].split("## Using it", 1)[0]
        rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
        check("doc table rows", len(rows), len(devices))

    if FAILURES:
        print(f"checks run: {CHECKS}")
        print(f"FAILURES:   {len(FAILURES)}\n")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"checks run: {CHECKS}")
    print("DEVICE REGISTRY: OK -- 34 models, tiers match the classification rule")
    return 0


def label(spec: tlcore.DeviceSpec) -> str:
    return spec.name


def test_device_registry() -> None:
    """pytest entry point."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
