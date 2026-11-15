"""The headset the user already paired: who is there, and is it a TL model?

The app used to ask for a Bluetooth address typed by hand, which is a poor first
run: the address is not printed anywhere obvious, and a typo looks exactly like
a headset that refuses the link. This module lists what the host already knows
about, so the app can offer a choice.

It also answers the other half of the question. ``tlcore.KNOWN_DEVICES`` is the
set of models the protocol work covers, so a paired device whose advertised name
matches one of those is marked **supported**; everything else is still listed --
a paired device is never hidden -- but marked as not a known TL model, because
connecting to it would be a guess.

Nothing here opens a connection, reads the headset, or changes a pairing.

**Platforms.** Only macOS enumerates today (:data:`PAIRING_AVAILABLE`). Elsewhere
:func:`paired_devices` returns an empty list and the app falls back to typing an
address, which still works.
"""
from __future__ import annotations

import re
import subprocess
import sys

from tlcore.devices import KNOWN_DEVICES, DeviceSpec

__all__ = [
    "PAIRING_AVAILABLE",
    "PairedDevice",
    "classify",
    "describe",
    "normalise_address",
    "paired_devices",
    "sorted_devices",
]

#: Whether this platform can list paired devices.
PAIRING_AVAILABLE = sys.platform == "darwin"

#: macOS prints and accepts ``AA-BB-CC-DD-EE-FF``; IOBluetooth answers
#: ``aa-bb-cc-dd-ee-ff``.  One form in, one form out.
_ADDRESS = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")

#: Bluetooth class-of-device major class 4 is "Audio/Video". A paired game
#: controller or keyboard is a real device but never a headset, and listing it
#: in a headset picker is noise. A device that classifies as a known TL model is
#: kept regardless, because the name is stronger evidence than a class byte.
_CLASS_AUDIO_VIDEO = 4


class PairedDevice:
    """One device the host has paired, with the support verdict."""

    __slots__ = ("name", "address", "connected", "spec")

    def __init__(self, name: str, address: str, connected: bool,
                 spec: DeviceSpec | None = None):
        self.name = name
        self.address = address
        self.connected = connected
        self.spec = spec

    @property
    def supported(self) -> bool:
        """Whether the advertised name matches a model this project covers."""
        return self.spec is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PairedDevice({self.name!r}, {self.address!r}, {self.supported})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, PairedDevice)
                and (self.name, self.address, self.connected, self.supported)
                == (other.name, other.address, other.connected, other.supported))


def normalise_address(address: str) -> str:
    """``aa:bb:cc:dd:ee:ff`` -> ``AA-BB-CC-DD-EE-FF``; anything else untouched."""
    text = (address or "").strip()
    if not _ADDRESS.match(text):
        return text
    return text.replace(":", "-").upper()


def classify(name: str) -> DeviceSpec | None:
    """The known model whose name matches ``name``, or ``None``.

    Matching is case-insensitive and ignores spacing, because hosts report the
    advertised name and the registry keeps the marketing name: ``REALME BUDS
    AIR7 PRO`` and ``realme Buds Air7 Pro`` are the same headset. The longest
    match wins, so ``realme Buds Air7`` cannot claim ``realme Buds Air7 Pro``.
    """
    wanted = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if not wanted:
        return None
    best: DeviceSpec | None = None
    for spec in KNOWN_DEVICES:
        key = re.sub(r"[^a-z0-9]", "", spec.name.lower())
        if not key or not wanted.startswith(key):
            continue
        if best is None or len(key) > len(re.sub(r"[^a-z0-9]", "", best.name.lower())):
            best = spec
    return best


def describe(device: PairedDevice) -> str:
    """The dropdown label: name, address, state, and whether it is a TL model."""
    parts = [device.name or "(unnamed device)", device.address]
    if device.connected:
        parts.append("connected")
    if device.supported:
        assert device.spec is not None
        parts.append(f"model {device.spec.model_id}" if device.spec.model_id
                     else "supported")
    else:
        parts.append("not a known TL model")
    return " — ".join(parts)


def sorted_devices(devices: list[PairedDevice]) -> list[PairedDevice]:
    """Supported first, then connected, then by name -- a stable, useful order."""
    return sorted(devices, key=lambda d: (not d.supported, not d.connected,
                                          (d.name or "").lower()))


def _macos_paired() -> list[PairedDevice]:
    """Ask IOBluetooth for the paired devices.  Read-only; no connection made."""
    try:
        import IOBluetooth
    except ImportError:
        return []
    try:
        found = IOBluetooth.IOBluetoothDevice.pairedDevices() or []
    except Exception:
        return []
    devices: list[PairedDevice] = []
    for entry in found:
        try:
            name = str(entry.name() or "")
            address = normalise_address(str(entry.addressString() or ""))
            connected = bool(entry.isConnected())
            major = int(entry.deviceClassMajor())
        except Exception:
            continue
        if not address:
            continue
        spec = classify(name)
        if spec is None and major != _CLASS_AUDIO_VIDEO:
            continue
        devices.append(PairedDevice(name, address, connected, spec))
    return devices


def _system_profiler_paired() -> list[PairedDevice]:
    """Fallback: parse ``system_profiler`` when IOBluetooth is unavailable."""
    try:
        done = subprocess.run(["system_profiler", "SPBluetoothDataType"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    devices: list[PairedDevice] = []
    name = ""
    for line in done.stdout.splitlines():
        stripped = line.strip()
        # A device block is "  <name>:" at two levels of indent, then its keys.
        if stripped.endswith(":") and not stripped.startswith(("Bluetooth",
                                                               "Controller",
                                                               "Device",
                                                               "Transport",
                                                               "Manufacturer")):
            name = stripped[:-1].strip()
            continue
        if stripped.startswith("Address:"):
            address = normalise_address(stripped.split(":", 1)[1].strip())
            if address:
                devices.append(PairedDevice(name, address, False, classify(name)))
    return devices


def paired_devices() -> list[PairedDevice]:
    """Every device the host has paired, best candidates first.

    Empty on a platform without enumeration, and empty when nothing is paired --
    both mean "let the user type an address", never an error.
    """
    if not PAIRING_AVAILABLE:
        return []
    devices = _macos_paired() or _system_profiler_paired()
    # De-duplicate on address: the two sources can disagree on ordering, never
    # on identity.
    seen: dict[str, PairedDevice] = {}
    for device in devices:
        seen.setdefault(device.address, device)
    return sorted_devices(list(seen.values()))
