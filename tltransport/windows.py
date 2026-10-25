"""Windows RFCOMM/SPP transport over WinSock (``AF_BTH`` / ``BTHPROTO_RFCOMM``).

Windows does expose Classic Bluetooth through WinSock, but Python's ``socket``
module only builds the Linux ``sockaddr_rc`` address, so the handful of calls
that matter are made directly against ``ws2_32.dll`` through :mod:`ctypes`.

Four details are load-bearing:

* **The address is a 48-bit integer, not a tuple.** ``SOCKADDR_BTH.btAddr`` takes
  the address as ``0xAABBCCDDEEFF`` for ``AA:BB:CC:DD:EE:FF`` -- the *first*
  octet is the most significant byte. This is the same convention as BlueZ's
  ``str2ba`` and the Windows ``BLUETOOTH_ADDRESS`` union, and it is the single
  easiest thing to get backwards.
* **Refusal and page timeout are different Winsock errors.** ``WSAECONNREFUSED``
  (10061) means the SPP server refused the channel; ``WSAETIMEDOUT`` /
  ``WSAEHOSTDOWN`` mean the buds never answered the page. They are raised as
  distinct errors so the caller can tell "retry the link" from "wake the buds".
* **A blocking ``connect`` returns only after the OS page timeout.** There is no
  separate timeout knob, so :meth:`WindowsTransport.open` retries a bounded
  number of times rather than hanging forever.
* **``read`` bounds the wait with ``SO_RCVTIMEO``.** A ``recv`` that times out
  returns ``WSAETIMEDOUT`` (or ``WSAEWOULDBLOCK``) and is reported as "no bytes",
  which is what the :class:`~tlcore.transport.Transport` contract asks for.

The module imports on every platform; only opening a link or running an SDP
lookup needs Windows, so the pure helpers (address parsing, error
classification) are exercised by ``tests/test_windows_transport.py`` on any host.
"""
from __future__ import annotations

import ctypes
import sys
import time

from tlcore.errors import (
    ChannelRefused,
    NotConnected,
    PageTimeout,
    TransportError,
)

__all__ = [
    "WindowsTransport",
    "parse_address",
    "format_address",
    "normalize_uuid",
    "guid_from_uuid",
    "classify_wsa_error",
]

# --- WinSock constants ----------------------------------------------------
AF_BTH = 32
BTHPROTO_RFCOMM = 3
SOCK_STREAM = 1
SOL_SOCKET = 0xFFFF
SO_RCVTIMEO = 0x1006
SO_SNDTIMEO = 0x1005
SO_ERROR = 0x1007
WSA_VERSION = 0x0202

NS_BTH = 16
LUP_RETURN_NAME = 0x0010
LUP_RETURN_ADDR = 0x0100
LUP_FLUSHCACHE = 0x1000

WSAEWOULDBLOCK = 10035
WSAEINPROGRESS = 10036
WSAEALREADY = 10037
WSAENETUNREACH = 10051
WSAETIMEDOUT = 10060
WSAECONNREFUSED = 10061
WSAEHOSTDOWN = 10064
WSAEHOSTUNREACH = 10065
WSAEFAULT = 10014
WSA_E_NO_MORE = 10110  # WSANO_DATA-adjacent; the provider's "end of results"

SOCKET_ERROR = -1

#: The SPP service-class GUID is the one the vendor app uses.
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

# --- structures -----------------------------------------------------------
class GUID(ctypes.Structure):
    """A Windows ``GUID`` (little-endian fields, big-endian ``Data4`` bytes)."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

class SOCKADDR_BTH(ctypes.Structure):
    """The Bluetooth socket address Windows' ``connect`` expects.

    Field order and widths are fixed by ``ws2bth.h``; ``port`` is the RFCOMM
    channel. The structure is **packed** -- ``ws2bth.h`` wraps it in
    ``#pragma pack(1)`` -- so it is 30 bytes, not the 40 a naturally aligned
    layout would give. Getting this wrong is silent: ``connect`` reads a zero
    address and fails with ``WSAEADDRNOTAVAIL``, and an SDP result parses to a
    zero channel.
    """

    _pack_ = 1
    _fields_ = [
        ("addressFamily", ctypes.c_uint16),
        ("btAddr", ctypes.c_ulonglong),
        ("serviceClassId", GUID),
        ("port", ctypes.c_uint32),
    ]

class WSADATA(ctypes.Structure):
    _fields_ = [
        ("wVersion", ctypes.c_uint16),
        ("wHighVersion", ctypes.c_uint16),
        ("szDescription", ctypes.c_char * 257),
        ("szSystemStatus", ctypes.c_char * 129),
        ("iMaxSockets", ctypes.c_uint16),
        ("iMaxUdpDg", ctypes.c_uint16),
        ("lpVendorInfo", ctypes.c_char_p),
    ]

class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]

class CSADDR_INFO(ctypes.Structure):
    _fields_ = [
        ("LocalAddr", SOCKET_ADDRESS),
        ("RemoteAddr", SOCKET_ADDRESS),
        ("iSocketType", ctypes.c_int),
        ("iProtocol", ctypes.c_int),
    ]

class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("pBlobData", ctypes.c_void_p)]

class WSAQUERYSETW(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("lpszServiceInstanceName", ctypes.c_wchar_p),
        ("lpServiceClassId", ctypes.POINTER(GUID)),
        ("lpVersion", ctypes.c_void_p),
        ("lpszComment", ctypes.c_wchar_p),
        ("dwNameSpace", ctypes.c_uint32),
        ("lpNSProviderId", ctypes.POINTER(GUID)),
        ("lpszContext", ctypes.c_wchar_p),
        ("dwNumberOfProtocols", ctypes.c_uint32),
        ("lpafpProtocols", ctypes.c_void_p),
        ("lpszQueryString", ctypes.c_wchar_p),
        ("dwNumberOfCsAddrs", ctypes.c_uint32),
        ("lpcsaBuffer", ctypes.POINTER(CSADDR_INFO)),
        ("dwOutputFlags", ctypes.c_uint32),
        ("lpBlob", ctypes.POINTER(BLOB)),
    ]

# --- pure helpers ---------------------------------------------------------
def parse_address(address: str) -> int:
    """``"AA:BB:CC:DD:EE:FF"`` (or ``-``/no separators) -> ``0xAABBCCDDEEFF``.

    The Bluetooth address is a big-endian integer: the first octet is the most
    significant byte, which is what ``SOCKADDR_BTH.btAddr`` wants.
    """
    text = address.replace("-", "").replace(":", "").replace(".", "").strip().upper()
    if len(text) != 12 or any(c not in "0123456789ABCDEF" for c in text):
        raise ValueError(f"not a Bluetooth address: {address!r}")
    return int(text, 16)

def format_address(value: int) -> str:
    """``0xAABBCCDDEEFF`` -> ``"AA:BB:CC:DD:EE:FF"`` (canonical form)."""
    text = f"{value & 0xFFFFFFFFFFFF:012X}"
    return ":".join(text[i:i + 2] for i in range(0, 12, 2))

def normalize_uuid(uuid: str) -> str:
    """Expand a short service-class UUID to the full 128-bit form."""
    text = uuid.replace("-", "").strip().upper()
    if len(text) == 32:
        return (f"{text[:8]}-{text[8:12]}-{text[12:16]}-"
                f"{text[16:20]}-{text[20:]}")
    if len(text) == 8:
        return f"{text}-0000-1000-8000-00805F9B34FB"
    if len(text) == 4:
        return f"0000{text}-0000-1000-8000-00805F9B34FB"
    return uuid

def guid_from_uuid(uuid: str) -> GUID:
    """Build the ``GUID`` for a full or short service-class UUID."""
    text = normalize_uuid(uuid).replace("-", "")
    if len(text) != 32:
        raise ValueError(f"not a service UUID: {uuid!r}")
    guid = GUID()
    guid.Data1 = int(text[0:8], 16)
    guid.Data2 = int(text[8:12], 16)
    guid.Data3 = int(text[12:16], 16)
    for index, byte in enumerate(bytes.fromhex(text[16:32])):
        guid.Data4[index] = byte
    return guid

def classify_wsa_error(code: int | None) -> type:
    """Map a Winsock error code to the ``tlcore.errors`` type it means.

    A *refusal* and a *page timeout* mean opposite things -- retry the link vs
    wake the device -- so they are never collapsed into one error.
    """
    if code == WSAECONNREFUSED:
        return ChannelRefused
    if code in (WSAETIMEDOUT, WSAEHOSTDOWN, WSAEHOSTUNREACH, WSAENETUNREACH):
        return PageTimeout
    return TransportError

# --- WinSock binding (lazy; Windows only) ---------------------------------
_WINSOCK = None

def _winsock():
    """Load and configure ``ws2_32`` once. Raises off Windows."""
    global _WINSOCK
    if _WINSOCK is not None:
        return _WINSOCK
    if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
        raise TransportError(
            f"the Windows transport is Windows-only (this is {sys.platform})")
    ws2 = ctypes.WinDLL("ws2_32")
    ws2.WSAStartup.argtypes = [ctypes.c_uint16, ctypes.POINTER(WSADATA)]
    ws2.WSAStartup.restype = ctypes.c_int
    ws2.WSACleanup.restype = ctypes.c_int
    ws2.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    ws2.socket.restype = ctypes.c_size_t
    ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    ws2.connect.restype = ctypes.c_int
    ws2.send.argtypes = [ctypes.c_size_t, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    ws2.send.restype = ctypes.c_int
    ws2.recv.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    ws2.recv.restype = ctypes.c_int
    ws2.closesocket.argtypes = [ctypes.c_size_t]
    ws2.closesocket.restype = ctypes.c_int
    ws2.setsockopt.argtypes = [ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_int]
    ws2.setsockopt.restype = ctypes.c_int
    ws2.WSAGetLastError.restype = ctypes.c_int
    ws2.WSALookupServiceBeginW.argtypes = [ctypes.POINTER(WSAQUERYSETW),
                                           ctypes.c_uint32,
                                           ctypes.POINTER(ctypes.c_void_p)]
    ws2.WSALookupServiceBeginW.restype = ctypes.c_int
    ws2.WSALookupServiceNextW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.POINTER(ctypes.c_uint32),
                                          ctypes.c_void_p]
    ws2.WSALookupServiceNextW.restype = ctypes.c_int
    ws2.WSALookupServiceEnd.argtypes = [ctypes.c_void_p]
    ws2.WSALookupServiceEnd.restype = ctypes.c_int
    data = WSADATA()
    if ws2.WSAStartup(WSA_VERSION, ctypes.byref(data)) != 0:
        raise TransportError("WSAStartup failed; is the Bluetooth stack running?")
    _WINSOCK = ws2
    return ws2

def _invalid_socket() -> int:
    return ctypes.c_size_t(-1).value

def sockaddr_bth(address: str, channel: int) -> SOCKADDR_BTH:
    """Build the address structure for ``address`` on RFCOMM ``channel``."""
    sa = SOCKADDR_BTH()
    sa.addressFamily = AF_BTH
    sa.btAddr = parse_address(address)
    sa.port = int(channel)
    return sa

class WindowsTransport:
    """A blocking RFCOMM transport implementing the ``tlcore.Transport`` contract."""

    def __init__(self, *, retries: int = 3, retry_delay: float = 0.5):
        self.retries = retries
        self.retry_delay = retry_delay
        self.address: str | None = None
        self.channel_id: int | None = None
        self._sock: int | None = None
        self._connected = False

    # -- discovery ---------------------------------------------------------
    def discover_channel(self, address: str, uuid: str) -> int | None:
        """Find the RFCOMM channel for ``uuid`` via a WinSock SDP lookup.

        The ``NS_BTH`` provider wants the address in its canonical
        colon-separated form as ``lpszContext``; the stripped form is rejected
        with ``WSAEINVAL``. The search is retried with a ``NULL`` context (all
        devices) and the results are still filtered by address.

        A device may advertise several services under one class UUID -- a Buds
        Air7 Pro answers four for SPP -- so the channel is returned only when it
        is unambiguous, and ``None`` means "pass ``--channel``". Returning the
        first match would silently open the wrong service.
        """
        ws2 = _winsock()
        guid = guid_from_uuid(uuid)
        wanted = parse_address(address)
        flags = LUP_RETURN_NAME | LUP_RETURN_ADDR | LUP_FLUSHCACHE
        colon_address = format_address(wanted)
        for context in (colon_address, None):
            query = WSAQUERYSETW()
            query.dwSize = ctypes.sizeof(WSAQUERYSETW)
            query.lpServiceClassId = ctypes.pointer(guid)
            query.dwNameSpace = NS_BTH
            query.lpszContext = context
            handle = ctypes.c_void_p()
            if ws2.WSALookupServiceBeginW(ctypes.byref(query), flags,
                                          ctypes.byref(handle)) != 0:
                continue
            try:
                return self._scan(ws2, handle, flags, wanted)
            finally:
                ws2.WSALookupServiceEnd(handle)
        return None

    @staticmethod
    def _scan(ws2, handle, flags: int, wanted: int) -> int | None:
        size = ctypes.c_uint32(0x1000)
        channels: set[int] = set()
        while True:
            buffer = ctypes.create_string_buffer(size.value)
            rc = ws2.WSALookupServiceNextW(handle, flags, ctypes.byref(size),
                                           buffer)
            if rc != 0:
                if ws2.WSAGetLastError() == WSAEFAULT:
                    continue  # size was raised to the required length
                break
            result = ctypes.cast(buffer, ctypes.POINTER(WSAQUERYSETW)).contents
            found = WindowsTransport._channel_from_result(result, wanted)
            if found is not None:
                channels.add(found)
        # One channel is an answer; several are not. See discover_channel.
        return channels.pop() if len(channels) == 1 else None

    @staticmethod
    def _channel_from_result(result: WSAQUERYSETW, wanted: int) -> int | None:
        for index in range(int(result.dwNumberOfCsAddrs)):
            csa = result.lpcsaBuffer[index]
            if not csa.RemoteAddr.lpSockaddr:
                continue
            sa = ctypes.cast(csa.RemoteAddr.lpSockaddr,
                             ctypes.POINTER(SOCKADDR_BTH)).contents
            if int(sa.btAddr) == wanted and sa.port:
                return int(sa.port)
        return None

    # -- lifecycle ---------------------------------------------------------
    def open(self, address: str, channel: int) -> None:
        ws2 = _winsock()
        sa = sockaddr_bth(address, channel)
        last_status: int | None = None
        for _ in range(max(1, self.retries)):
            sock = ws2.socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM)
            if sock == _invalid_socket():
                last_status = ws2.WSAGetLastError()
                time.sleep(self.retry_delay)
                continue
            if ws2.connect(sock, ctypes.byref(sa), ctypes.sizeof(sa)) == 0:
                self._sock = sock
                self.address = address
                self.channel_id = channel
                self._connected = True
                return
            last_status = ws2.WSAGetLastError()
            ws2.closesocket(sock)
            time.sleep(self.retry_delay)
        self._raise_for_status(last_status, address, channel)

    @staticmethod
    def _raise_for_status(status: int | None, address: str, channel: int) -> None:
        kind = classify_wsa_error(status)
        if kind is ChannelRefused:
            raise ChannelRefused(
                f"RFCOMM channel {channel} on {address} was refused "
                f"(WSAECONNREFUSED {WSAECONNREFUSED}); re-establish the link "
                "and retry")
        if kind is PageTimeout:
            raise PageTimeout(
                f"device {address} did not answer the page (WSA error {status}); "
                "it may be asleep, out of range, or off")
        detail = f"WSA error {status}" if status is not None else "no socket"
        raise TransportError(
            f"could not open RFCOMM channel {channel} on {address} ({detail})")

    def close(self) -> None:
        if self._sock is not None:
            try:
                _winsock().closesocket(self._sock)
            except Exception:
                pass
        self._sock = None
        self._connected = False

    def is_connected(self) -> bool:
        return bool(self._sock is not None and self._connected)

    # -- I/O ---------------------------------------------------------------
    def write(self, data: bytes) -> None:
        if not self.is_connected():
            raise NotConnected("RFCOMM channel is not open")
        ws2 = _winsock()
        data = bytes(data)
        sent = 0
        while sent < len(data):
            count = ws2.send(self._sock, data[sent:], len(data) - sent, 0)
            if count == SOCKET_ERROR:
                code = ws2.WSAGetLastError()
                self._connected = False
                raise TransportError(f"RFCOMM write failed (WSA error {code})")
            sent += count

    def read(self, timeout: float) -> bytes:
        if not self.is_connected():
            raise NotConnected("RFCOMM channel is not open")
        ws2 = _winsock()
        millis = max(1, int(max(0.0, timeout) * 1000))
        value = ctypes.c_uint32(millis)
        ws2.setsockopt(self._sock, SOL_SOCKET, SO_RCVTIMEO,
                       ctypes.byref(value), ctypes.sizeof(value))
        buffer = ctypes.create_string_buffer(4096)
        count = ws2.recv(self._sock, ctypes.byref(buffer), len(buffer), 0)
        if count == SOCKET_ERROR:
            code = ws2.WSAGetLastError()
            if code in (WSAETIMEDOUT, WSAEWOULDBLOCK):
                return b""
            self._connected = False
            raise NotConnected(f"RFCOMM read failed (WSA error {code})")
        if count == 0:
            self._connected = False
            raise NotConnected("RFCOMM channel was closed by the peer")
        return bytes(buffer.raw[:count])

    def __repr__(self) -> str:
        return (f"<WindowsTransport {self.address} ch={self.channel_id} "
                f"open={self.is_connected()}>")
