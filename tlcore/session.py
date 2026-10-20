"""A request/response session over a :class:`~tlcore.transport.Transport`.

This is the connection state machine the architecture assigns to ``tlcore``: it
frames commands, reads and decodes replies, keeps unsolicited ``0x0204`` pushes
separate from replies, negotiates the capability bitmap, and applies the
in-ear write gate for noise mode. It contains **no platform imports** -- it only
calls ``write``/``read``/``is_connected`` on whatever transport it is handed, so
the same code runs against real hardware and against a recorded replay.
"""
from __future__ import annotations

import time
from typing import Optional

from .commands import (
    ALWAYS_ON,
    CMD_COMPACTNESS_DETECT,
    CMD_GET_AI_STATUS,
    CMD_GET_ALL_EQ_INFO,
    CMD_GET_EQ_MODE,
    CMD_GET_HIGH_VOLUME,
    CMD_GET_KEY_FUNCTION,
    CMD_GET_NOISE_REDUCTION,
    CMD_GET_REMOTE_CAPABILITY,
    CMD_SET_AI_STATUS,
    CMD_SET_EQ_MODE,
    CMD_SET_HIGH_VOLUME,
    CMD_SET_KEY_FUNCTION,
    CMD_SET_NOISE_REDUCTION,
    RESPONSE_BIT,
    parse_capabilities,
)
from .errors import DeviceRejected, NotConnected, NotWorn, PacketError, Timeout
from .noise import NOISE_FIELD_MODE, NOISE_MODE_NAMES
from .payloads import (
    payload_compactness_detect,
    payload_set_ai_status,
    payload_set_eq_mode,
    payload_set_high_volume,
    payload_set_noise_mode,
)
from .wire import StreamDecoder, build_frames, build_packet

class Session:
    """Drives a transport with TL packets; adds no I/O of its own."""

    def __init__(self, transport, *, max_frame_size: int = 512,
                 cmd_interval: float = 0.01, read_timeout: float = 1.5):
        self.transport = transport
        #: Air7 Pro pacing; set to 0 for replay/tests.
        self.cmd_interval = cmd_interval
        self.max_frame_size = max_frame_size
        self.read_timeout = read_timeout
        self.decoder = StreamDecoder()
        self.capabilities: Optional[dict] = None
        #: Unsolicited packets seen while waiting for a reply.
        self.events: list[dict] = []

    # -- raw I/O -----------------------------------------------------------
    def send(self, cmd: int, payload: bytes = b"", type_byte: int = 0) -> list[bytes]:
        """Frame and write ``cmd``; returns the frames sent."""
        if not self.transport.is_connected():
            raise NotConnected("transport is not connected")
        frames = build_frames(build_packet(cmd, payload, type_byte), self.max_frame_size)
        for frame in frames:
            self.transport.write(frame)
            if self.cmd_interval:
                time.sleep(self.cmd_interval)
        return frames

    def _read_packets(self, timeout: float) -> list[dict]:
        """Read once and return any decoded packets (stops at the first batch)."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            data = self.transport.read(min(remaining, 0.25))
            if not data:
                time.sleep(0.002)
                continue
            packets = [ev["packet"] for ev in self.decoder.feed(data)
                       if ev.get("kind") == "packet"]
            if packets:
                return packets

    def request(self, cmd: int, payload: bytes = b"", *, timeout: Optional[float] = None,
                expect: Optional[int] = None, type_byte: int = 0) -> dict:
        """Send ``cmd`` and return the matching reply packet.

        Non-matching packets (unsolicited pushes) are collected in
        :attr:`events`. Raises :class:`~tlcore.errors.Timeout` if no reply
        arrives.
        """
        expect = (cmd | RESPONSE_BIT) if expect is None else expect
        self.send(cmd, payload, type_byte)
        deadline = time.monotonic() + (self.read_timeout if timeout is None else timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Timeout(f"no 0x{expect:04X} reply to 0x{cmd:04X}")
            found: Optional[dict] = None
            for pkt in self._read_packets(remaining):
                if found is None and pkt["cmd"] == expect:
                    found = pkt
                else:
                    # keep pushes that shared the read with the reply
                    self.events.append(pkt)
            if found is not None:
                return found

    def listen(self, timeout: float) -> list[dict]:
        """Read and decode for up to ``timeout`` seconds; return every packet."""
        deadline = time.monotonic() + timeout
        packets: list[dict] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            batch = self._read_packets(remaining)
            if not batch:
                break
            packets.extend(batch)
        return packets

    # -- status helpers ----------------------------------------------------
    @staticmethod
    def status_of(packet: dict) -> int:
        payload = packet.get("payload") or b""
        return payload[0] if payload else 0

    def check_status(self, packet: dict) -> int:
        """Raise on a non-zero reply status; return 0 otherwise.

        ``0x0e`` becomes :class:`~tlcore.errors.NotWorn` because it is the
        expected in-ear gate, not a failure.
        """
        status = self.status_of(packet)
        if status == 0x0E:
            raise NotWorn(f"0x{packet['cmd']:04X}: buds are not worn (status 0x0e)",
                          cmd=packet["cmd"], status=status)
        if status != 0:
            raise DeviceRejected(
                f"0x{packet['cmd']:04X}: device rejected with status 0x{status:02X}",
                cmd=packet["cmd"], status=status)
        return status

    # -- capability negotiation -------------------------------------------
    def negotiate_capabilities(self, *, timeout: Optional[float] = None) -> dict:
        """Send an empty ``0x0100`` and decode the ``0x8100`` capability bitmap."""
        reply = self.request(CMD_GET_REMOTE_CAPABILITY, b"", timeout=timeout)
        self.capabilities = parse_capabilities(reply["payload"])
        return self.capabilities

    def supports(self, cmd: int) -> bool:
        """Whether the device accepts ``cmd`` (always-on, or advertised)."""
        if cmd in ALWAYS_ON:
            return True
        if self.capabilities is None:
            return False
        return cmd in set(self.capabilities["commands"])

    # -- noise mode --------------------------------------------------------
    def read_noise_mode(self, *, timeout: Optional[float] = None) -> int:
        """Read the active noise mode (``0x010C`` selector ``1``).

        Reply body is ``[status][field][len][value]``; the value is last.
        """
        reply = self.request(CMD_GET_NOISE_REDUCTION, bytes([NOISE_FIELD_MODE]),
                             timeout=timeout)
        self.check_status(reply)
        payload = reply["payload"]
        if len(payload) < 4:
            raise PacketError(f"0x810C body too short for a mode value: {payload.hex()}")
        return payload[3]

    def set_noise_mode(self, value: int, *, timeout: Optional[float] = None) -> int:
        """Write the active noise mode. Raises :class:`NotWorn` if not in-ear."""
        if value not in NOISE_MODE_NAMES:
            raise ValueError(f"0x{value:02X} is not a known noise mode: "
                             + ", ".join(f"0x{k:02X}" for k in sorted(NOISE_MODE_NAMES)))
        reply = self.request(CMD_SET_NOISE_REDUCTION, payload_set_noise_mode(value),
                             timeout=timeout)
        return self.check_status(reply)

    # -- sound effect / EQ -------------------------------------------------
    def read_eq_mode(self, *, timeout: Optional[float] = None) -> int:
        """Read the active sound-effect/EQ preset (``0x010F``)."""
        reply = self.request(CMD_GET_EQ_MODE, b"", timeout=timeout)
        self.check_status(reply)
        payload = reply["payload"]
        if len(payload) < 2:
            raise PacketError(f"0x810F body too short for an EQ mode: {payload.hex()}")
        return payload[1]

    def set_eq_mode(self, mode: int, *, timeout: Optional[float] = None) -> int:
        """Write the active sound-effect/EQ preset (``0x0406``).

        **Verified live** on the Air7 Pro: the value ranges 0..6 and ``0x010F``
        reads it back identity.  Selecting a mode other than 4 clears the
        ``isSelected`` flag in the ``0x0122`` EQ list.
        """
        reply = self.request(CMD_SET_EQ_MODE, payload_set_eq_mode(mode), timeout=timeout)
        return self.check_status(reply)

    def read_eq_info(self, *, timeout: Optional[float] = None) -> dict:
        """Read the EQ preset/custom list (``0x0122``)."""
        from .decoders import parse_eq_info

        reply = self.request(CMD_GET_ALL_EQ_INFO, b"", timeout=timeout)
        self.check_status(reply)
        return parse_eq_info(reply["payload"])

    # -- high volume -------------------------------------------------------
    def read_high_volume(self, *, timeout: Optional[float] = None) -> bool:
        """Read the high-volume (loudness boost) status (``0x0110``).

        **Verified live** on the Air7 Pro: the reply is ``[status][0|1]``.
        """
        reply = self.request(CMD_GET_HIGH_VOLUME, b"", timeout=timeout)
        self.check_status(reply)
        payload = reply["payload"]
        if len(payload) < 2:
            raise PacketError(f"0x8110 body too short for a high-volume flag: {payload.hex()}")
        return bool(payload[1])

    def set_high_volume(self, enabled: bool, *, timeout: Optional[float] = None) -> int:
        """Write the high-volume status (``0x0407``).

        **Verified live** on the Air7 Pro (2026-10-20): the ``0x0110`` read-back
        follows the write, and the restore returns it.  Accepted only while the
        buds are worn (else :class:`~tlcore.errors.NotWorn`).
        """
        reply = self.request(CMD_SET_HIGH_VOLUME, payload_set_high_volume(enabled),
                             timeout=timeout)
        return self.check_status(reply)

    # -- AI / translation status -------------------------------------------
    def read_ai_status(self, *, timeout: Optional[float] = None) -> dict:
        """Read the AI/translation status (``0x0131``): ``[status][state][type]``.

        **Verified live** on the Air7 Pro (2026-10-20): returns
        ``{"state": 1, "type": 1}`` after ``0x0428 01 01``.
        """
        reply = self.request(CMD_GET_AI_STATUS, b"", timeout=timeout)
        self.check_status(reply)
        payload = reply["payload"]
        if len(payload) < 3:
            raise PacketError(f"0x8131 body too short for an AI status: {payload.hex()}")
        return {"state": payload[1], "type": payload[2]}

    def set_ai_status(self, state: int, ai_type: int, *,
                      timeout: Optional[float] = None) -> int:
        """Write the AI/translation status (``0x0428``), ``[state][type]``.

        **Verified live** on the Air7 Pro (2026-10-20): ``state=1`` activates
        the session for ``ai_type`` and the ``0x0131`` read-back follows.
        ``state=0`` deactivates only with the matching active ``ai_type``.
        """
        reply = self.request(CMD_SET_AI_STATUS, payload_set_ai_status(state, ai_type),
                             timeout=timeout)
        return self.check_status(reply)

    # -- compactness / in-ear detect ---------------------------------------
    def set_compactness_detect(self, action: bool, *,
                               timeout: Optional[float] = None) -> int:
        """Start (``True``) or stop (``False``) in-ear detection (``0x0405``).

        **Verified live** on the Air7 Pro (2026-10-20): the trigger is accepted
        and the firmware answers on the ``0x0204`` event-``0x04`` push (two
        ``[earSide][value]`` records), not on a read.  Sensor only -- it drives
        no audio.  Use the push decoder
        (:func:`tlcore.decoders.parse_compactness_event`) to read the result.
        """
        reply = self.request(CMD_COMPACTNESS_DETECT,
                             payload_compactness_detect(action), timeout=timeout)
        return self.check_status(reply)

    def start_compactness_detect(self, *, timeout: Optional[float] = None) -> int:
        """Start in-ear detection (``0x0405 01``).  See
        :meth:`set_compactness_detect`."""
        return self.set_compactness_detect(True, timeout=timeout)

    def stop_compactness_detect(self, *, timeout: Optional[float] = None) -> int:
        """Stop in-ear detection (``0x0405 00``).  See
        :meth:`set_compactness_detect`."""
        return self.set_compactness_detect(False, timeout=timeout)

    # -- key functions / gestures ------------------------------------------
    def read_key_function(self, *, timeout: float | None = None) -> dict:
        """Read the key-function/gesture table (``0x0108``).

        Returns ``{"status", "count", "entries", "records"}`` -- ``entries``
        are :class:`~tlcore.buttons.KeyFunctionInfo` records; ``records`` the
        same rows as hex.  Pairs with :meth:`set_key_function`.
        """
        from .buttons import parse_key_function_table

        reply = self.request(CMD_GET_KEY_FUNCTION, b"", timeout=timeout)
        self.check_status(reply)
        return parse_key_function_table(reply["payload"])

    def set_key_function(self, entries, *, timeout: float | None = None) -> int:
        """Write the **whole** key-function table (``0x0401``).

        ``entries`` is a sequence of :class:`~tlcore.buttons.KeyFunctionInfo`
        (or 4-byte blobs).  The command replaces every row, so read the table
        first and patch it -- see :func:`tlcore.patch_key_functions` and
        :func:`tlcore.disable_long_press`.
        """
        from .payloads import payload_set_key_function

        blobs = [e.to_bytes() if hasattr(e, "to_bytes") else bytes(e) for e in entries]
        reply = self.request(CMD_SET_KEY_FUNCTION, payload_set_key_function(blobs),
                             timeout=timeout)
        return self.check_status(reply)

    # -- bulk status -------------------------------------------------------
    def read_status(self, *, timeout: Optional[float] = None) -> dict:
        """Poll model, firmware, battery, wear state, noise mode, and use time.

        The order matches a real device conversation (and the recorded fixtures)
        so a replay reproduces it without skipping records.
        """
        from .decoders import (parse_battery, parse_device_version, parse_earbud_status,
                               parse_model_id, parse_use_time)

        out: dict = {}
        # ``0x0101`` is the version/property query; the body is not decoded yet,
        # so expose the raw bytes and the first little-endian halfword.
        prop = self.request(0x0101, b"", timeout=timeout)
        prop_pl = prop["payload"]
        out["version"] = {
            "status": self.status_of(prop),
            "value": int.from_bytes(prop_pl[1:3], "little") if len(prop_pl) >= 3 else None,
            "raw": prop_pl.hex(),
        }
        for key, cmd, decoder in (
            ("model", 0x0103, parse_model_id),
            ("firmware", 0x0105, parse_device_version),
            ("battery", 0x0106, parse_battery),
            ("wear", 0x0109, parse_earbud_status),
        ):
            out[key] = decoder(self.request(cmd, b"", timeout=timeout)["payload"])
        out["noise_mode"] = self.read_noise_mode(timeout=timeout)
        out["use_time"] = parse_use_time(
            self.request(0x0128, b"", timeout=timeout)["payload"])
        return out
