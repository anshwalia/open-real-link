"""Per-platform byte movers implementing the :class:`tlcore.Transport` contract.

There is no plugin registry: :func:`for_platform` picks one implementation at
runtime, and :func:`open_transport` constructs and opens it.

* ``macos``   -- IOBluetooth RFCOMM (``pyobjc-framework-IOBluetooth``)
* ``windows`` -- WinSock RFCOMM (``AF_BTH`` / ``BTHPROTO_RFCOMM``), via ctypes
* ``replay``  -- an offline recording under ``fixtures/``, needs no hardware

**Only macOS and Windows can reach a real device.** ``replay`` is available
everywhere, but it is not a fallback for a live connection -- it needs a
recording, and asking it for one that does not exist is what used to happen off
macOS: ``auto`` resolved to ``replay`` and the user got ``write past the end of
the recording``. A platform with no live transport now says so up front, before
any I/O.
"""
from __future__ import annotations

import sys
from typing import Optional

from tlcore.errors import TransportError, TransportUnavailable
from tlcore.transport import Transport

__all__ = ["Transport", "SPP_UUID", "OPPO_UUID", "APPLE_UUID",
           "for_platform", "open_transport", "available_transports",
           "platform_transport"]

#: Well-known service-class UUIDs seen on the Air7 Pro's SDP record.
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"          # what the vendor app uses
OPPO_UUID = "0000079A-D102-11E1-9B23-00025B00A5A5"         # OPPO interaction
APPLE_UUID = "2CFE21DF-2515-4FDB-8886-F12C4D67927C"        # Apple accessory


#: Platforms with a transport that can reach a real device.
LIVE_PLATFORMS = {"darwin": "macos", "win32": "windows"}


def platform_transport() -> Optional[str]:
    """The name of this platform's **live** transport, or ``None`` if it has none.

    ``None`` is a real answer, not a failure: Linux can still replay a recording,
    it just cannot open a radio link.
    """
    return LIVE_PLATFORMS.get(sys.platform)


def available_transports() -> list[str]:
    """Transport names usable on this host.  ``replay`` is always available."""
    names = ["replay"]
    live = platform_transport()
    if live is not None:
        names.append(live)
    return names


def for_platform(name: Optional[str] = None):
    """Return the ``Transport`` class for ``name`` (default: this platform).

    ``auto`` (and ``None``) means *the live transport for this platform*, so on
    a platform without one this raises :class:`~tlcore.errors.TransportUnavailable`
    rather than quietly handing back the replay transport.
    """
    if name in (None, "auto"):
        name = platform_transport()
        if name is None:
            raise TransportUnavailable(
                f"no live transport on this platform ({sys.platform}): the live "
                "implementations are macOS IOBluetooth and Windows WinSock. Use "
                "--replay FILE to run against a recording instead.")
    if name == "macos":
        if platform_transport() != "macos":
            # say so before the import, so the failure is not an ImportError
            # about pyobjc that reads like a missing dependency
            raise TransportUnavailable(
                f"the macOS transport is macOS-only (this is {sys.platform}); "
                "no other platform can open the RFCOMM link yet.")
        from .macos import MacOSTransport
        return MacOSTransport
    if name == "windows":
        if platform_transport() != "windows":
            raise TransportUnavailable(
                f"the Windows transport is Windows-only (this is {sys.platform}); "
                "no other platform can open the RFCOMM link yet.")
        from .windows import WindowsTransport
        return WindowsTransport
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
            # A TransportError, not a bare ValueError: the front ends catch
            # TLError and print one sentence, and a discovery miss should say
            # how to get past it (the SPP channel is 12 on an Air7 Pro).
            raise TransportError(
                f"no RFCOMM channel for {uuid or SPP_UUID} on {address}; "
                "pass --channel N if you know it")
    transport.open(address, channel)
    return transport
