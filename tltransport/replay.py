"""An offline :class:`~tlcore.transport.Transport` over a recorded capture.

Each line of a fixture is ``{"ts": float, "dir": "tx"|"rx", "hex": "..."}``.
A :meth:`ReplayTransport.write` is matched against the next recorded ``tx``
(strict by default, so a drifting caller fails loudly); :meth:`read` returns the
recorded ``rx`` chunks in order. Any ``rx`` that the recording places before the
next ``tx`` -- an unsolicited push -- is buffered and delivered on the next read.

This lets the whole stack -- protocol, session, CLI, UI -- run with no hardware,
which is how it is tested and how the app is developed without wearing the buds.

    python3 -c "from tltransport.replay import ReplayTransport as R; \\
        t=R.from_fixture('spp-smoketest.jsonl'); t.open('replay', 12)"
"""
from __future__ import annotations

import json
import pathlib
from collections import deque
from collections.abc import Iterable
from typing import Optional, Union

from tlcore.errors import NotConnected, TLError

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures"

__all__ = ["ReplayTransport", "ReplayError", "ReplayMismatch"]


class ReplayError(TLError):
    """Base class for replay failures."""


class ReplayMismatch(ReplayError):
    """A write did not match the recorded transmission at the cursor."""


class ReplayTransport:
    """Replays a recorded tx/rx conversation as a blocking transport."""

    def __init__(self, records: Iterable[tuple[str, bytes]] = (), *,
                 strict: bool = True, loop: bool = False, channel: int = 12,
                 source: Optional[str] = None):
        self.records: list[tuple[str, bytes]] = [
            (direction, bytes(data)) for direction, data in records
        ]
        self.strict = strict
        self.loop = loop
        self.channel = channel
        self.source = source
        self.idx = 0
        self._inbound: deque[bytes] = deque()
        self._connected = False
        self.writes: list[bytes] = []

    # -- constructors ------------------------------------------------------
    @classmethod
    def from_jsonl(cls, path: Union[str, pathlib.Path], **kwargs) -> ReplayTransport:
        path = pathlib.Path(path)
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records.append((rec["dir"], bytes.fromhex(rec["hex"])))
        return cls(records, source=str(path), **kwargs)

    @classmethod
    def from_fixture(cls, name: str, **kwargs) -> ReplayTransport:
        return cls.from_jsonl(FIXTURES_DIR / name, **kwargs)

    # -- Transport ---------------------------------------------------------
    def discover_channel(self, address: str, uuid: str) -> Optional[int]:
        return self.channel

    def open(self, address: str, channel: int) -> None:
        self.channel = channel
        self.idx = 0
        self.writes.clear()
        self._inbound.clear()
        self._connected = True

    def write(self, data: bytes) -> None:
        if not self._connected:
            raise NotConnected("replay transport is not open")
        data = bytes(data)
        self._buffer_unsolicited()
        if self.idx >= len(self.records):
            if self.loop:
                self.idx = 0
                self._buffer_unsolicited()
            elif self.strict:
                raise ReplayError("write past the end of the recording")
            else:
                self.writes.append(data)
                return
        direction, recorded = self.records[self.idx]
        if direction != "tx":
            if self.strict:
                raise ReplayMismatch(
                    f"record {self.idx}: wrote {data.hex()} but the next record is rx")
            self.writes.append(data)
            return
        if self.strict and recorded != data:
            raise ReplayMismatch(
                f"record {self.idx}: wrote {data.hex()}, recording has {recorded.hex()}")
        self.idx += 1
        self.writes.append(data)

    def read(self, timeout: float) -> bytes:
        if not self._connected:
            return b""
        self._buffer_unsolicited()
        return self._inbound.popleft() if self._inbound else b""

    def close(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # -- internals ---------------------------------------------------------
    def _buffer_unsolicited(self) -> None:
        """Move recorded ``rx`` that precedes the next ``tx`` into the inbox."""
        while self.idx < len(self.records) and self.records[self.idx][0] == "rx":
            self._inbound.append(self.records[self.idx][1])
            self.idx += 1

    def __repr__(self) -> str:
        return (f"<ReplayTransport {self.source or 'inline'} "
                f"at {self.idx}/{len(self.records)} open={self._connected}>")
