"""macOS RFCOMM/SPP transport over IOBluetooth (``pyobjc-framework-IOBluetooth``).

macOS exposes a paired device's services through SDP; we open the RFCOMM channel
whose service-class UUID matches, then move raw frame bytes. Three details are
load-bearing (they cost real debugging time in the original tools):

* IOBluetooth delivers ``rfcommChannelData:data:length:`` on the **run loop** of
  the opening thread, so :meth:`read` pumps the run loop instead of sleeping.
* ``openRFCOMMChannelSync`` returns an IOReturn that is unreliable on its own --
  a channel can report ``0xE00002BC`` yet still open -- so success is decided by
  ``channel.isOpen()``.
* a *refusal* (``0xE00002BC``) and a *page timeout* (``0xE00002D6``) mean
  opposite things and are raised as distinct errors. IOReturns arrive signed, so
  they are compared after ``& 0xFFFFFFFF``.
"""
from __future__ import annotations

import time
from typing import Optional

from tlcore.errors import (
    ChannelRefused,
    NotConnected,
    PageTimeout,
    TransportError,
)

try:
    import IOBluetooth
    import objc
    from Foundation import NSDate, NSDefaultRunLoopMode, NSObject, NSRunLoop
except ImportError as exc:  # pragma: no cover - environment dependent
    raise ImportError(
        "the macOS transport needs pyobjc-framework-IOBluetooth "
        "(pip install 'open-real-link[macos]')"
    ) from exc

__all__ = ["MacOSTransport", "sdp_services"]

IORETURN_OK = 0
IORETURN_CHANNEL_REFUSED = 0xE00002BC
IORETURN_PAGE_TIMEOUT = 0xE00002D6

#: SDP service-class UUIDs observed on TL headsets; used for channel discovery.
KNOWN_UUIDS = {
    "00001101-0000-1000-8000-00805F9B34FB": "SPP (Serial Port)",
    "0000079A-D102-11E1-9B23-00025B00A5A5": "OPPO interaction",
    "2CFE21DF-2515-4FDB-8886-F12C4D67927C": "Apple accessory",
}


def _uuid_of(element) -> str:
    """Normalise an SDP service-class element to a full UUID string.

    Returns the raw UUID, never a friendly name -- discovery matches on it, so a
    label here would silently break channel lookup.
    """
    try:
        text = str(element)
    except Exception:
        return "?"
    if "(" in text and text.endswith(")"):
        text = text[text.index("(") + 1:-1]
    hexs = text.replace(" ", "").upper()
    if len(hexs) == 8:
        return f"{hexs}-0000-1000-8000-00805F9B34FB"
    if len(hexs) == 4:
        return f"0000{hexs}-0000-1000-8000-00805F9B34FB"
    if len(hexs) == 32:
        return f"{hexs[:8]}-{hexs[8:12]}-{hexs[12:16]}-{hexs[16:20]}-{hexs[20:]}"
    return text


def sdp_services(address: str) -> list[dict]:
    """Enumerate a device's SDP records (read-only)."""
    device = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(address)
    if device is None:
        raise ValueError(f"no Bluetooth device with address {address}")
    services = []
    for index, record in enumerate(device.getServices() or []):
        buf = bytearray(1)
        rc = record.getRFCOMMChannelID_(buf)
        channel = buf[0] if rc == IORETURN_OK else None
        uuid = "?"
        element = record.getAttributeDataElement_(1)  # ServiceClassIDList
        if element is not None:
            uuid = _uuid_of(element)
        services.append({"index": index, "channel": channel, "uuid": uuid,
                         "name": KNOWN_UUIDS.get(uuid, uuid)})
    return services


class _Delegate(NSObject):
    """Collects inbound RFCOMM data and tracks channel state."""

    def init(self):
        self = objc.super(_Delegate, self).init()
        self.buf = bytearray()
        self.closed = False
        return self

    def rfcommChannelData_data_length_(self, channel, data, length):
        try:
            self.buf += bytes(data[:length])
        except Exception:
            try:
                self.buf += bytes(bytearray(data)[:length])
            except Exception:
                pass

    def rfcommChannelClosed_(self, channel):
        self.closed = True


class MacOSTransport:
    """A blocking RFCOMM transport implementing the ``tlcore.Transport`` contract."""

    def __init__(self, *, retries: int = 6, retry_delay: float = 0.5):
        self.retries = retries
        self.retry_delay = retry_delay
        self.address: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.device = None
        self.channel = None
        self._delegate: Optional[_Delegate] = None

    # -- discovery ---------------------------------------------------------
    def discover_channel(self, address: str, uuid: str) -> Optional[int]:
        needle = uuid.replace("-", "").upper()
        for svc in sdp_services(address):
            if svc["channel"] is None:
                continue
            if svc["uuid"].replace("-", "").upper().startswith(needle):
                return svc["channel"]
        return None

    # -- lifecycle ---------------------------------------------------------
    def open(self, address: str, channel: int) -> None:
        device = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(address)
        if device is None:
            raise TransportError(f"no Bluetooth device with address {address}")
        last_status = None
        for _ in range(max(1, self.retries)):
            delegate = _Delegate.alloc().init()
            result = device.openRFCOMMChannelSync_withChannelID_delegate_(
                None, channel, delegate)
            if isinstance(result, tuple) and len(result) == 2:
                status, rfcomm = result
            else:
                status, rfcomm = result, None
            last_status = status
            if rfcomm is not None and rfcomm.isOpen():
                rfcomm.setDelegate_(delegate)
                self.address = address
                self.channel_id = channel
                self.device = device
                self.channel = rfcomm
                self._delegate = delegate  # keep alive: GC would close the channel
                return
            time.sleep(self.retry_delay)
        self._raise_for_status(last_status, address, channel)

    @staticmethod
    def _raise_for_status(status, address: str, channel: int) -> None:
        code = None if status is None else int(status) & 0xFFFFFFFF
        if code == IORETURN_CHANNEL_REFUSED:
            raise ChannelRefused(
                f"RFCOMM channel {channel} on {address} was refused (0xE00002BC); "
                "re-establish the link and retry")
        if code == IORETURN_PAGE_TIMEOUT:
            raise PageTimeout(
                f"device {address} did not answer the page (0xE00002D6); it may be "
                "asleep, out of range, or off")
        detail = f"IOReturn 0x{code:08X}" if code is not None else "no channel object"
        raise TransportError(f"could not open RFCOMM channel {channel} on {address} ({detail})")

    def close(self) -> None:
        try:
            if self.channel is not None:
                self.channel.closeChannel()
        except Exception:
            pass
        self.channel = None
        self._delegate = None

    def is_connected(self) -> bool:
        return bool(self.channel is not None and self.channel.isOpen())

    # -- I/O ---------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not self.is_connected():
            raise NotConnected("RFCOMM channel is not open")
        data = bytes(data)
        rc = self.channel.writeSync_length_(data, len(data))
        if rc != IORETURN_OK:
            raise TransportError(
                f"RFCOMM write failed (IOReturn 0x{int(rc) & 0xFFFFFFFF:08X})")

    def read(self, timeout: float) -> bytes:
        if not self.is_connected():
            raise NotConnected("RFCOMM channel is not open")
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._delegate is not None and self._delegate.buf:
                out = bytes(self._delegate.buf)
                self._delegate.buf.clear()
                return out
            if self._delegate is not None and self._delegate.closed:
                raise NotConnected("RFCOMM channel was closed by the peer")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            self._pump_runloop(min(remaining, 0.05))

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _pump_runloop(seconds: float) -> None:
        """Let IOBluetooth dispatch delegate callbacks (never a bare sleep)."""
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(seconds))

    def reset_device(self, address: str, *, settle: float = 1.5,
                     reconnect: bool = True) -> bool:
        """Drop and optionally re-establish the ACL link (extra method, not part
        of the six-method contract). Clears transient channel refusals."""
        device = IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(address)
        if device is None:
            return False
        try:
            device.closeConnection()
        except Exception:
            pass
        deadline = time.time() + settle
        while time.time() < deadline and device.isConnected():
            self._pump_runloop(0.1)
        if reconnect and not device.isConnected():
            try:
                device.openConnection()
            except Exception:
                pass
            deadline = time.time() + settle
            while time.time() < deadline and not device.isConnected():
                self._pump_runloop(0.1)
        return bool(device.isConnected())

    def __repr__(self) -> str:
        return (f"<MacOSTransport {self.address} ch={self.channel_id} "
                f"open={self.is_connected()}>")
