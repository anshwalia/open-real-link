"""The two wire layers: OPPOv1 transport framing, and the TL packet header.

    application  Packet        cmd uint16 LE | type | len uint16 LE | payload
    transport    OPPOv1Wrapper 0xAA | LEB128 len | control | 0x00 | [FSN] | ..

Plus :class:`StreamDecoder`, which turns an arbitrarily-chunked byte stream
into decoded packets -- a serial/RFCOMM read may return a partial frame,
several frames, or one message split across frames.
"""
from __future__ import annotations

import struct

from .commands import CLASS_NAMES, cmd_class, is_response
from .errors import FrameError, PacketError

SOF = 0xAA


def build_packet(cmd: int, payload: bytes = b"", type_byte: int = 0) -> bytes:
    """Packet.a(): 5-byte little-endian header + payload."""
    payload = bytes(payload)
    return struct.pack("<HBH", cmd & 0xFFFF, type_byte & 0xFF, len(payload)) + payload


def parse_packet(data: bytes) -> dict:
    """Packet.<init>(byte[]). Raises PacketError when shorter than 5 bytes.

    PacketError subclasses ValueError, matching the reference codec's contract.
    """
    if len(data) < 5:
        raise PacketError("packet shorter than 5-byte header")
    cmd, type_byte, length = struct.unpack("<HBH", data[:5])
    payload = data[5:]
    return {
        "cmd": cmd,
        "cmd_hex": f"0x{cmd:04X}",
        "type": type_byte,
        "length": length,
        "payload": payload,
        "truncated": len(payload) < length,
        "is_response": is_response(cmd),
        "manager": CLASS_NAMES.get(cmd_class(cmd), "unknown"),
    }


def varint_encode(value: int) -> bytes:
    """OPPOv1Wrapper.o(int): LEB128, little-endian groups of 7 bits."""
    if value == 0:
        return b"\x00"
    out = bytearray()
    while value:
        b = value & 0x7F
        value >>= 7
        out.append(b | 0x80 if value else b)
    return bytes(out)


def varint_decode(data: bytes, offset: int = 0) -> tuple[int, int]:
    """OPPOv1Wrapper.n(byte[], int) -> (value, bytes_consumed)."""
    value = 0
    shift = 0
    i = 0
    while True:
        b = data[offset + i]
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, i + 1
        i += 1
        shift += 7


def _varint_size(frame_index: int, remaining: int, max_frame_size: int) -> int:
    """OPPOv1Wrapper.j(int i, int length, int maxFrameSize)."""
    if max_frame_size <= 129:
        return 1
    if frame_index == 0:
        return 1 if remaining <= 125 else 2
    return 1 if remaining <= 124 else 2


def build_frames(data: bytes, max_frame_size: int = 512,
                 flag_a: bool = False, flag_b: bool = False) -> list[bytes]:
    """OPPOv1Wrapper.m(byte[], boolean, boolean): split payload into frames."""
    if not data:
        return []
    frames: list[bytes] = []
    remaining = len(data)
    offset = 0
    index = 0
    while remaining > 0:
        varint_size = _varint_size(index, remaining, max_frame_size)
        budget = max_frame_size - varint_size
        room_no_fsn = budget - 3

        if index == 0:
            if remaining <= room_no_fsn:
                payload_len = remaining
                status = 0
                has_fsn = False
            else:
                payload_len = budget - 4
                status = 1
                has_fsn = True
        else:
            payload_len = budget - 4
            if remaining <= payload_len:
                status = 3
                payload_len = remaining
            else:
                status = 2
            has_fsn = True

        body_len = payload_len + (3 if has_fsn else 2)
        frame = bytearray(body_len + 1 + varint_size)
        frame[0] = SOF
        v = varint_encode(body_len)
        frame[1:1 + len(v)] = v
        control = (0x08 if flag_a else 0) | (0x04 if flag_b else 0) | status
        frame[varint_size + 1] = control
        frame[varint_size + 2] = 0x00          # reserved
        cursor = varint_size + 3
        if has_fsn:
            frame[cursor] = index
            cursor = varint_size + 4
        frame[cursor:cursor + payload_len] = data[offset:offset + payload_len]

        frames.append(bytes(frame))
        index += 1
        remaining -= payload_len
        offset += payload_len
    return frames


def parse_frame(data: bytes) -> dict:
    """OPPOv1Wrapper.f(byte[]): parse a single transport frame."""
    if len(data) < 4:
        raise FrameError("frame shorter than 4 bytes")
    if data[0] != SOF:
        raise FrameError(f"bad SOF: 0x{data[0]:02X} (expected 0xAA)")
    declared, consumed = varint_decode(data, 1)
    varint_size = consumed if declared > 127 else 1
    control = data[varint_size + 1]
    status = control & 0x03
    has_fsn = status != 0
    start = varint_size + (4 if has_fsn else 3)
    return {
        "declared_body_len": declared,
        "control": control,
        "flag_a": bool(control & 0x08),
        "flag_b": bool(control & 0x04),
        "frame_status": status,
        "has_fsn": has_fsn,
        "fsn": data[varint_size + 3] if has_fsn else None,
        "payload": data[start:],
    }


def deframe(chunks: list[bytes]) -> bytes:
    """Reassemble a payload from consecutive transport frames."""
    return b"".join(parse_frame(c)["payload"] for c in chunks)


class StreamDecoder:
    def __init__(self, raw: bool = False):
        self.raw = raw
        self._buf = bytearray()
        self._chunks: list[bytes] = []

    def feed(self, data: bytes) -> list[dict]:
        self._buf += bytes(data)
        events = []
        while True:
            ev = self._next_raw() if self.raw else self._next_frame()
            if ev is None:
                break
            events.append(ev)
        return events

    def pending(self) -> int:
        """How many bytes are held back waiting for the rest of a frame."""
        return len(self._buf)

    def flush(self) -> list[dict]:
        """Emit buffered leftovers as junk (call once at end of stream)."""
        if not self._buf:
            return []
        junk = bytes(self._buf)
        self._buf.clear()
        return [{"kind": "junk", "data": junk}]

    def _packet_event(self, payload: bytes, frames: int, transport: str) -> dict:
        try:
            pkt = parse_packet(payload)
        except ValueError as exc:
            return {"kind": "junk", "data": payload, "error": str(exc),
                    "transport": transport}
        return {"kind": "packet", "transport": transport,
                "packet": pkt, "frames": frames}

    def _next_raw(self) -> dict | None:
        buf = self._buf
        for off in range(0, max(0, len(buf) - 4)):
            cmd, _type, length = struct.unpack("<HBH", bytes(buf[off:off + 5]))
            if cmd_class(cmd) not in CLASS_NAMES:
                continue
            if len(buf) - off < 5 + length:
                continue
            if off:
                junk = bytes(buf[:off])
                del buf[:off]
                return {"kind": "junk", "data": junk}
            raw = bytes(buf[:5 + length])
            del buf[:5 + length]
            return self._packet_event(raw, 0, "raw")
        if len(buf) > 4:
            junk = bytes(buf[:-4])
            del buf[:-4]
            return {"kind": "junk", "data": junk}
        return None

    def _next_frame(self) -> dict | None:
        buf = self._buf
        idx = buf.find(SOF)
        if idx < 0:
            junk = bytes(buf)
            del buf[:]
            return {"kind": "junk", "data": junk} if junk else None
        if idx:
            junk = bytes(buf[:idx])
            del buf[:idx]
            return {"kind": "junk", "data": junk}
        if len(buf) < 3:
            return None
        try:
            declared, consumed = varint_decode(bytes(buf), 1)
        except IndexError:
            return None
        varint_size = consumed if declared > 127 else 1
        total = 1 + varint_size + declared
        if len(buf) < total:
            return None
        frame = bytes(buf[:total])
        del buf[:total]
        try:
            pf = parse_frame(frame)
        except ValueError as exc:
            return {"kind": "junk", "data": frame, "error": str(exc)}
        if not pf["has_fsn"]:
            return self._packet_event(pf["payload"], 1, "oppov1")
        if pf["frame_status"] == 1:
            self._chunks = [frame]
            return {"kind": "frame", "frame": pf}
        self._chunks.append(frame)
        if pf["frame_status"] == 3:
            count = len(self._chunks)
            payload = deframe(self._chunks)
            self._chunks = []
            return self._packet_event(payload, count, "oppov1")
        return {"kind": "frame", "frame": pf}
