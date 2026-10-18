"""Exception hierarchy.

The wire layer (``tlcore.wire``) raises :class:`FrameError` / :class:`PacketError`,
both of which subclass :class:`ValueError`.  That is deliberate: the reference
codec raised ``ValueError``, and ``tests/parity_tl_frame.py`` asserts that
contract still holds.  Anything that used to ``except ValueError`` keeps working.

Everything above the wire uses the richer types so callers can react to the
conditions that actually matter in the field -- the difference between "the
buds are not in your ears" and "the link died" is the difference between a
useful error message and a bug report.
"""

from __future__ import annotations


class TLError(Exception):
    """Base class for everything this library raises."""


# --- wire ----------------------------------------------------------------
class FrameError(TLError, ValueError):
    """A transport (OPPOv1) frame was malformed."""


class PacketError(TLError, ValueError):
    """A TL packet was malformed."""


# --- device --------------------------------------------------------------
class DeviceRejected(TLError):
    """The device answered a request with a non-zero status.

    ``cmd``/``status`` are filled in where the reply carried them.
    """

    def __init__(self, message: str, cmd: int | None = None, status: int | None = None):
        super().__init__(message)
        self.cmd = cmd
        self.status = status


class NotWorn(DeviceRejected):
    """``0x8404 0e`` -- the buds are not in an ear.

    Noise-mode writes are gated on in-ear detection, so this is an expected
    state, not a failure: tell the user to put the buds in.
    """


# --- transport -----------------------------------------------------------
class TransportError(TLError):
    """Base class for link-level failures."""


class NotConnected(TransportError):
    """No link is open."""


class ChannelRefused(TransportError):
    """The device refused the RFCOMM channel (macOS IOReturn ``0xE00002BC``).

    Distinct from :class:`PageTimeout`: the radio link is up but the SPP server
    is not accepting.  The cure is to re-establish the link (``reset_device``),
    not to keep retrying.
    """


class PageTimeout(TransportError):
    """The device did not answer the page (macOS IOReturn ``0xE00002D6``).

    The device is probably asleep, out of range, or powered off.
    """


class Timeout(TransportError):
    """A read or write did not complete in time."""
