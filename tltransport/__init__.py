"""Per-platform byte movers implementing the :class:`tlcore.Transport` contract.

There is no plugin registry: :func:`for_platform` picks one implementation at
runtime, and :func:`open_transport` constructs and opens it.

* ``macos``  -- IOBluetooth RFCOMM (``pyobjc-framework-IOBluetooth``)
* ``replay`` -- an offline recording under ``fixtures/``, needs no hardware
"""
from __future__ import annotations

import sys
from typing import Optional

from tlcore.transport import Transport

__all__ = ["Transport", "SPP_UUID", "OPPO_UUID", "APPLE_UUID",
           "for_platform", "open_transport", "available_transports"]

#: Well-known service-class UUIDs seen on the Air7 Pro's SDP record.
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"          # what the vendor app uses
OPPO_UUID = "0000079A-D102-11E1-9B23-00025B00A5A5"         # OPPO interaction
APPLE_UUID = "2CFE21DF-2515-4FDB-8886-F12C4D67927C"        # Apple accessory


def available_transports() -> list[str]:
    """Transport names usable on this host."""
    names = ["replay"]
    if sys.platform == "darwin":
        names.append("macos")
    return names


def for_platform(name: Optional[str] = None):
    """Return the ``Transport`` class for ``name`` (default: this platform)."""
    if name in (None, "auto"):
        name = "macos" if sys.platform == "darwin" else "replay"
    if name == "macos":
        from .macos import MacOSTransport
        return MacOSTransport
    if name == "replay":
        from .replay import ReplayTransport
        return ReplayTransport
    raise ValueError(f"unknown transport {name!r}; try one of {available_transports()}")


def open_transport(address: str, *, channel: Optional[int] = None,
                   uuid: Optional[str] = None, name: Optional[str] = None,
                   reset: bool = False, **kwargs) -> Transport:
    """Construct and open a transport to ``address``.

    ``channel`` may be given directly; otherwise the channel is discovered for
    ``uuid`` (defaulting to :data:`SPP_UUID`). With ``reset`` the ACL link is
    dropped and re-established first, which clears a transient channel refusal.
    """
    cls = for_platform(name)
    transport = cls(**kwargs)
    if reset and hasattr(transport, "reset_device"):
        transport.reset_device(address)
    if channel is None:
        channel = transport.discover_channel(address, uuid or SPP_UUID)
        if channel is None:
            raise ValueError(f"no RFCOMM channel for {uuid or SPP_UUID} on {address}")
    transport.open(address, channel)
    return transport
