"""The abstract transport contract.

``tlcore`` never imports a platform binding. It drives bytes through an object
with these six methods; :mod:`tltransport` provides the concrete implementations
(macOS IOBluetooth, an offline replay over recorded fixtures) and tests provide
in-memory fakes.

Three of the requirements are protocol requirements, not style:

* :meth:`Transport.is_connected` must be a **local property read** -- no radio
  traffic -- so link state can be polled while waiting for the device to raise
  an ACL.
* :meth:`Transport.read` must **pump the platform run loop** rather than sleep;
  on macOS a bare sleep starves IOBluetooth and delivers no bytes.
* :meth:`Transport.open` must raise a distinct error for a *refusal* versus a
  *timeout* (see :class:`tlcore.errors.ChannelRefused` / ``PageTimeout``).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class Transport(Protocol):
    """A byte pipe to a paired device. Implementations live in ``tltransport``."""

    def discover_channel(self, address: str, uuid: str) -> Optional[int]:
        """Return the RFCOMM channel for ``uuid`` on ``address``, or ``None``."""
        ...

    def open(self, address: str, channel: int) -> None:
        """Open the RFCOMM channel. Raises a :class:`~tlcore.errors.TransportError`."""
        ...

    def write(self, data: bytes) -> None:
        """Send one transport frame."""
        ...

    def read(self, timeout: float) -> bytes:
        """Return available bytes, blocking up to ``timeout``; ``b""`` on none."""
        ...

    def close(self) -> None:
        """Close the channel; safe to call more than once."""
        ...

    def is_connected(self) -> bool:
        """Link state as a local property read. Must not put bytes on the radio."""
        ...
