"""What the host is actually streaming: the A2DP codec, and the link quality.

Everything else in this package moves bytes to the **headset**. This module
reads the **host**, and it is the only place that does, because a Bluetooth
audio codec is negotiated between two Bluetooth stacks and is never reported
over the protocol: no TL command selects or reports one. It lives here, next to
the platform transports, because it is platform-specific host I/O of the same
kind -- ``log show`` instead of an RFCOMM socket -- and both the CLI (``tlcli
audio``) and the app (Expert -> Audio link) need it without either importing the
other.

macOS exposes no public API for the negotiated codec either, but ``bluetoothd``
logs it. A controller-info query (which ``system_profiler`` issues, and which
macOS itself issues when the Bluetooth panel is opened) emits an ``AuLQ``
record::

    AuLQ [{Time: ..., AoS 1, BtRt 224 Kbps, Codc AAC-LC, Freq ISM2.4,
           DvNm 'Buds', JtBf 150 ms, NsFl -99, RtmR 23%, RSSI -45, SNR 54}]

and the AVDTP setup logs one line per remote endpoint::

    Audio Codec ID MPEG-2,4 AAC for remote SEID 2

Two fields say whether the answer is *live*: ``AoS 0`` with ``BtRt 0 Kbps`` means
no stream is running and ``Codc`` is the codec that was configured, while
``AoS 1`` with a non-zero bitrate is traffic.

**Read-only.** The commands here query the host's own Bluetooth daemon. Nothing
is sent to the headset, nothing is written anywhere, and no root is needed.

**Platforms.** Only macOS logs this; :data:`A2DP_AVAILABLE` is ``False``
elsewhere and every function returns ``None``, so a caller on another platform
shows "not available" rather than a wrong number.

The endpoint labels are the host's own words. A vendor-specific codec prints as
``VENDOR-SPECIFIC`` and this module does not guess which vendor codec it is --
that would be a claim the host never made.
"""
from __future__ import annotations

import re
import subprocess
import sys

__all__ = [
    "A2DP_AVAILABLE",
    "DEFAULT_WINDOW_MINUTES",
    "parse_endpoints",
    "parse_link",
    "read_link",
    "summary",
]

#: Whether this platform can report the A2DP link at all.
A2DP_AVAILABLE = sys.platform == "darwin"

#: How far back to look for the ``AuLQ`` record.  Generous on purpose: the
#: record is written at connect time, so a link that has been up for a while
#: still has one, and a wider window costs nothing when it does not.
DEFAULT_WINDOW_MINUTES = 15

#: ``AuLQ [{Time: ..., AoS 1, BtRt 224 Kbps, Codc AAC-LC, ..., DvNm 'Buds'}]``
_AULQ = re.compile(r"AuLQ \[\{([^\]]*)\}\]")

#: ``Audio Codec ID MPEG-2,4 AAC for remote SEID 2`` -- the codec name may
#: contain a space, so it is matched lazily rather than as one token.
_SEID = re.compile(r"Audio Codec ID (.+?) for remote SEID (\d+)")

#: Seconds to allow the log query, and the controller-info query behind it.
_LOG_TIMEOUT = 20.0
_QUERY_TIMEOUT = 25.0


def _run(argv: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _log(minutes: int) -> str:
    return _run(
        ["log", "show", "--last", f"{max(1, int(minutes))}m",
         "--predicate", 'process == "bluetoothd"'],
        _LOG_TIMEOUT,
    )


def _query() -> None:
    """Ask bluetoothd for controller info, which is what logs the ``AuLQ``."""
    _run(["system_profiler", "SPBluetoothDataType"], _QUERY_TIMEOUT)


def _fields(body: str) -> dict[str, str]:
    """``"AoS 1, BtRt 224 Kbps, DvNm 'Buds'"`` -> ``{"AoS": "1", ...}``."""
    out: dict[str, str] = {}
    for part in body.split(", "):
        key, _, value = part.partition(" ")
        if key.endswith(":"):
            key = key[:-1]
        out[key] = value.strip()
    return out


def parse_link(text: str) -> dict | None:
    """The most recent ``AuLQ`` record in ``text``, or ``None``.

    ``text`` is raw ``bluetoothd`` log output.  The newest record wins: an
    older one describes a codec that may no longer be in use.
    """
    found = _AULQ.findall(text or "")
    if not found:
        return None
    fields = _fields(found[-1])
    codec = fields.get("Codc", "")
    bitrate = fields.get("BtRt", "")
    if not codec:
        return None
    return {
        "device": fields.get("DvNm", "").strip("'"),
        "codec": codec,
        "bitrate": bitrate,
        "jitter": fields.get("JtBf", ""),
        "rssi": fields.get("RSSI", ""),
        "snr": fields.get("SNR", ""),
        "retransmit": fields.get("RtmR", ""),
        "live": fields.get("AoS", "0") not in ("0", "") and bitrate not in ("", "0 Kbps"),
        "observed": fields.get("Time", ""),
    }


def parse_endpoints(text: str) -> list[tuple[int, str]]:
    """``[(seid, codec), ...]`` from the AVDTP setup lines, ascending by SEID.

    These are the endpoints the *headset* offered at connect time, named by the
    host: ``SBC``, ``MPEG-2,4 AAC``, ``VENDOR-SPECIFIC``.
    """
    seen: dict[int, str] = {}
    for codec, seid in _SEID.findall(text or ""):
        seen[int(seid)] = codec.strip()
    return sorted(seen.items())


def read_link(minutes: int = DEFAULT_WINDOW_MINUTES, *, trigger: bool = True) -> dict | None:
    """Read the current link, or ``None`` when there is nothing to report.

    The log is read first, because the record is usually already there. Only if
    that finds nothing is a controller-info query issued and the log read again,
    which costs a couple of seconds -- the point is that a normal refresh stays
    fast.
    """
    if not A2DP_AVAILABLE:
        return None
    text = _log(minutes)
    link = parse_link(text)
    if link is None and trigger:
        _query()
        text = _log(minutes)
        link = parse_link(text)
    if link is None:
        return None
    link["endpoints"] = parse_endpoints(text)
    return link


def summary(link: dict | None) -> str:
    """One line for a status bar, e.g. ``AAC-LC · 224 Kbps · live``."""
    if not link:
        return "no A2DP link reported"
    state = "live" if link.get("live") else "configured, no stream"
    return f"{link.get('codec', '?')} · {link.get('bitrate', '?')} · {state}"
