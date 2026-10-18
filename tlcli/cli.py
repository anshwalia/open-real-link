"""``tlcli`` -- a command-line front end over ``tlcore`` + ``tltransport``.

It is the first real consumer of the protocol core, and doubles as the hardware
test harness. Every subcommand also runs against a recorded fixture with
``--replay``, so it can be exercised with no device present:

    tlcli --replay fixtures/spp-smoketest.jsonl info
    tlcli --address AA-BB-CC-DD-EE-FF mode set anc
    tlcli --address AA-BB-CC-DD-EE-FF send 0x010C 01 --read 1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import Optional

import tlcore as t
from tltransport import SPP_UUID, open_transport
from tltransport.replay import FIXTURES_DIR, ReplayError, ReplayTransport

#: Commands that drive loud test tones (hearing / ear-scan). Refused unless
#: ``--force``; see docs/protocol.md section 7.
DANGEROUS = {
    0x011E: "getEarScanData",
    0x011F: "getEarScanFilterData",
    0x040E: "switchProcessHearingDetectionParams",
    0x0415: "sendProcessEarScanData",
}

MODE_ALIASES = {
    "normal": t.NOISE_MODE_NORMAL,
    "off": t.NOISE_MODE_NORMAL,
    "transparency": t.NOISE_MODE_TRANSPARENCY,
    "ambient": t.NOISE_MODE_TRANSPARENCY,
    "light": t.NOISE_MODE_ANC_LIGHT,
    "deep": t.NOISE_MODE_ANC_DEEP,
    "medium": t.NOISE_MODE_ANC_MEDIUM,
    "smart": t.NOISE_MODE_ANC_SMART,
    "adaptive": t.NOISE_MODE_ANC_SMART,
    "anc": t.NOISE_MODE_ANC,
}


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------
def parse_mode(text: str) -> int:
    key = text.strip().lower()
    if key in MODE_ALIASES:
        return MODE_ALIASES[key]
    try:
        value = int(text, 0)
    except ValueError:
        raise SystemExit(f"unknown noise mode {text!r}; use a name "
                         f"({', '.join(sorted(MODE_ALIASES))}) or a value like 0x20")
    if value not in t.NOISE_MODE_NAMES:
        raise SystemExit(f"0x{value:02X} is not a known noise mode: "
                         + ", ".join(f"0x{k:02X}" for k in sorted(t.NOISE_MODE_NAMES)))
    return value


def parse_cmd(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError:
        raise SystemExit(f"command id must be numeric, e.g. 0x0404 (got {text!r})")
    if not 0 <= value <= 0xFFFF:
        raise SystemExit(f"command id out of range: {text!r}")
    return value


def open_session(args) -> tuple[object, t.Session]:
    """Build (and open) the transport and a Session over it."""
    if args.replay:
        path = pathlib.Path(args.replay)
        if not path.exists():
            path = FIXTURES_DIR / args.replay
        if not path.exists():
            raise SystemExit(f"no such fixture: {args.replay}")
        transport = ReplayTransport.from_jsonl(path, strict=not args.loose)
        transport.open("replay", args.channel or 12)
    else:
        if args.transport == "replay":
            raise SystemExit("replay needs a recording; pass --replay FILE")
        if not args.address:
            raise SystemExit("--address is required for a live device "
                             "(or use --replay FILE)")
        transport = open_transport(args.address, channel=args.channel,
                                   uuid=args.uuid, name=args.transport,
                                   reset=args.reset)
    session = t.Session(transport, max_frame_size=args.frame_size,
                        cmd_interval=args.interval_ms / 1000.0,
                        read_timeout=args.timeout)
    return transport, session


def noise_name(value: int) -> str:
    return t.NOISE_MODE_NAMES.get(value, f"0x{value:02X}")


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def packet_dict(packet: dict) -> dict:
    return {"cmd": packet["cmd"], "cmd_hex": packet["cmd_hex"],
            "length": packet["length"], "payload": packet["payload"].hex()}


def print_packet(packet: dict) -> None:
    payload = packet["payload"].hex(" ")
    name = t.CMD_NAMES.get(packet["cmd"], "")
    print(f'  0x{packet["cmd"]:04X}  {t.CLASS_NAMES.get(t.cmd_class(packet["cmd"]), "?"):<24} '
          f'len={packet["length"]:<4} [{payload}]{"" if not name else "  " + name}')


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_services(args) -> int:
    if args.transport == "replay" or args.replay:
        raise SystemExit("services needs a live macOS device")
    if sys.platform != "darwin":
        raise SystemExit("SDP enumeration is only implemented on macOS")
    if not args.address:
        raise SystemExit("--address is required to list services")
    from tltransport.macos import sdp_services

    services = sdp_services(args.address)
    if args.json:
        print(json.dumps(services, indent=2))
        return 0
    print(f"SDP services on {args.address}:")
    for svc in services:
        name = svc.get("name", "")
        suffix = f"  ({name})" if name and name != svc["uuid"] else ""
        print(f"  record[{svc['index']:2}]  channel={svc['channel']}  {svc['uuid']}{suffix}")
    return 0


def cmd_caps(args) -> int:
    transport, session = open_session(args)
    try:
        caps = session.negotiate_capabilities()
    finally:
        transport.close()
    if args.json:
        print(json.dumps({"status": caps["status"], "ok": caps["ok"],
                          "commands": caps["commands"], "bits": caps["bits"]}, indent=2))
        return 0
    print(f"capability report: status={caps['status']} ok={caps['ok']} "
          f"commands={len(caps['commands'])}")
    for cmd in caps["commands"]:
        tag = " (always-on)" if cmd in caps["always_on"] else ""
        name = t.CMD_NAMES.get(cmd, "")
        print(f"  0x{cmd:04X}  {t.CLASS_NAMES.get(t.cmd_class(cmd), '?'):<24} {name}{tag}")
    return 0


def cmd_info(args) -> int:
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        status = session.read_status()
    finally:
        transport.close()
    if args.json:
        print(json.dumps(status, indent=2, default=str))
        return 0
    model = status["model"]["model_id"]
    print(f"model     : 0x{model:06X}" if model is not None else "model     : unknown")
    print(f"firmware  : {', '.join(status['firmware']['versions']) or 'unknown'}")
    version = status.get("version", {}).get("value")
    if version is not None:
        print(f"version   : {version} (0x{version:04X})")
    battery = status["battery"]["devices"]
    print("battery   : " + "  ".join(
        f"{dev}={d['level']}%{'^' if d['charging'] else ''}" for dev, d in battery.items()))
    wear = status["wear"]
    print(f"wear      : {'in-ear' if wear['in_ear'] else 'not worn'}  {wear['devices']}")
    mode = status["noise_mode"]
    print(f"noise mode: 0x{mode:02X}  {noise_name(mode)}")
    use = status["use_time"]["devices"]
    print("use time  : " + "  ".join(f"{d}={sec}s" for d, sec in use.items()))
    return 0


def cmd_backup(args) -> int:
    """Capture the device's user settings into a JSON snapshot."""
    transport, session = open_session(args)
    try:
        snapshot = t.capture(session)
    finally:
        transport.close()
    text = json.dumps(snapshot, indent=2)
    if args.file == "-":
        print(text)
        return 0
    pathlib.Path(args.file).write_text(text + "\n")
    print(f"backup written to {args.file}")
    print(f"  device    : {snapshot['device']}")
    print(f"  captured  : {len(snapshot['raw'])} responses")
    print(f"  restorable: {', '.join(sorted(snapshot['settings'])) or 'none'}")
    if snapshot["skipped"]:
        print(f"  skipped   : {len(snapshot['skipped'])}")
    return 0


def cmd_restore(args) -> int:
    """Re-apply the restorable user settings from a backup snapshot."""
    data = json.loads(pathlib.Path(args.file).read_text())
    transport, session = open_session(args)
    try:
        report = t.restore(session, data, dry_run=args.dry_run)
    finally:
        transport.close()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for item in report["applied"]:
        tag = "DRY-RUN" if item.get("dry_run") else "APPLIED"
        print(f"  [{tag}] {item['setting']}")
    for item in report["not_restored"]:
        print(f"  [SKIP]    {item['setting']} ({item['reason']})")
    if not report["applied"]:
        print("(nothing to restore)")
    return 0


#: Canonical order for the ``mode cycle`` hardware test.
MODE_CYCLE = (
    t.NOISE_MODE_NORMAL, t.NOISE_MODE_TRANSPARENCY, t.NOISE_MODE_ANC_LIGHT,
    t.NOISE_MODE_ANC_DEEP, t.NOISE_MODE_ANC_MEDIUM, t.NOISE_MODE_ANC_SMART,
)

#: Short spoken cues for ``mode cycle --say`` (macOS ``say``).
SPOKEN_MODES = {
    t.NOISE_MODE_NORMAL: "normal mode",
    t.NOISE_MODE_TRANSPARENCY: "transparency",
    t.NOISE_MODE_ANC_LIGHT: "a n c light",
    t.NOISE_MODE_ANC_DEEP: "a n c deep",
    t.NOISE_MODE_ANC_MEDIUM: "a n c medium",
    t.NOISE_MODE_ANC_SMART: "a n c smart",
}


#: Trailing silence appended to every spoken cue. When the output is a
#: Bluetooth headset, ``say`` is torn down just before the final audio buffer
#: drains, clipping the last phoneme (e.g. "light" -> "lig"). Padding the
#: utterance means that clipped sliver is silence instead of a real word.
SAY_TAIL_SILENCE_MS = 1000


def announce(text: str) -> None:
    """Speak ``text`` with the macOS ``say`` command; silently no-op elsewhere."""
    if sys.platform != "darwin":
        return
    cue = f"{text} [[slnc {SAY_TAIL_SILENCE_MS}]]"
    try:
        subprocess.run(["say", cue], check=False)
    except OSError:
        pass


def cmd_mode(args) -> int:
    tokens = list(args.mode_args)
    if not tokens or tokens[0].lower() == "get":
        return _mode_get(args)
    if tokens[0].lower() == "cycle":
        return _mode_cycle(args)
    if tokens[0].lower() == "set":
        if len(tokens) != 2:
            raise SystemExit("usage: mode set <value>")
        return _mode_set(args, tokens[1])
    if len(tokens) != 1:
        raise SystemExit("usage: mode [get | set | cycle] <value>")
    return _mode_set(args, tokens[0])


def _mode_get(args) -> int:
    transport, session = open_session(args)
    try:
        mode = session.read_noise_mode(timeout=args.timeout)
    finally:
        transport.close()
    if args.json:
        print(json.dumps({"noise_mode": mode, "name": noise_name(mode)}, indent=2))
    else:
        print(f"noise mode: 0x{mode:02X}  {noise_name(mode)}")
    return 0


def _mode_set(args, text: str) -> int:
    wanted = parse_mode(text)
    transport, session = open_session(args)
    try:
        session.set_noise_mode(wanted, timeout=args.timeout)
        # read back: the 0x0204 push is racy, the read is authoritative
        actual = session.read_noise_mode(timeout=args.timeout)
    finally:
        transport.close()
    if args.json:
        print(json.dumps({"requested": wanted, "noise_mode": actual,
                          "name": noise_name(actual)}, indent=2))
    else:
        print(f"set 0x{wanted:02X} ({noise_name(wanted)}); "
              f"device now 0x{actual:02X} ({noise_name(actual)})")
    return 0 if actual == wanted else 1


def _mode_cycle(args) -> int:
    """Write every mode, read each back, verify, and restore the original."""
    transport, session = open_session(args)
    rows: list[dict] = []
    original = None
    restored = False
    try:
        original = session.read_noise_mode(timeout=args.timeout)
        for value in MODE_CYCLE:
            # Speak *before* the write: the headset is usually the Mac's audio
            # output, and reconfiguring ANC clips whatever is playing, which
            # would cut a cue spoken after the change.
            if args.say:
                announce(SPOKEN_MODES.get(value, noise_name(value)))
                time.sleep(0.5)  # let the Bluetooth audio buffer drain before the write
            try:
                session.set_noise_mode(value, timeout=args.timeout)
                actual = session.read_noise_mode(timeout=args.timeout)
                rows.append({"requested": value, "actual": actual,
                             "ok": actual == value, "note": ""})
                if args.dwell:
                    time.sleep(args.dwell)  # hold each mode so it is audible
            except t.NotWorn:
                rows.append({"requested": value, "actual": None, "ok": False,
                             "note": "not worn"})
                break
            except t.TLError as exc:
                rows.append({"requested": value, "actual": None, "ok": False,
                             "note": str(exc)})
                break
        if original is not None and rows and rows[-1]["actual"] != original:
            try:
                session.set_noise_mode(original, timeout=args.timeout)
                restored = True
            except t.TLError:
                restored = False
    finally:
        transport.close()

    failed = [row for row in rows if not row["ok"]]
    if args.json:
        print(json.dumps({"original": original, "restored": restored,
                          "steps": rows}, indent=2))
    else:
        print(f"noise mode cycle (starting 0x{original:02X} {noise_name(original)})"
              if original is not None else "noise mode cycle")
        for row in rows:
            req, actual = row["requested"], row["actual"]
            state = "PASS" if row["ok"] else "FAIL"
            detail = (f"-> 0x{actual:02X} ({noise_name(actual)})"
                      if actual is not None else row["note"])
            print(f"  [{state}] 0x{req:02X} {noise_name(req):<20} {detail}")
        if restored:
            print(f"restored 0x{original:02X} ({noise_name(original)})")
        print("RESULT:", "FAIL" if failed else "OK")
    return 1 if failed else 0


def _status_summary(status: dict) -> str:
    model = status["model"]["model_id"]
    battery = status["battery"]["devices"]
    bat = " ".join(f"{dev}={val['level']}%" for dev, val in battery.items())
    mode = status["noise_mode"]
    firmware = ",".join(status["firmware"]["versions"]) or "?"
    wear = "in-ear" if status["wear"]["in_ear"] else "not worn"
    return (f"model=0x{model:06X} fw={firmware} mode=0x{mode:02X}({noise_name(mode)}) "
            f"battery[{bat}] wear={wear}")


def cmd_smoke(args) -> int:
    """A single end-to-end check: connect, negotiate, poll, and (optionally) write."""
    transport, session = open_session(args)
    rows: list[list] = []

    def run(label, fn):
        try:
            value = fn()
            rows.append([label, "PASS", value])
            return value
        except t.NotWorn:
            rows.append([label, "SKIP", "buds not worn"])
            return None
        except Exception as exc:  # noqa: BLE001 - report every failure as a row
            rows.append([label, "FAIL", f"{type(exc).__name__}: {exc}"])
            return None

    try:
        caps = run("capability bitmap (0x8100)", session.negotiate_capabilities)
        status = run("status poll (0x0101..0x0128)",
                     lambda: session.read_status(timeout=args.timeout))
        if caps is not None:
            rows[0][2] = f"{len(caps['commands'])} commands, status={caps['status']}"
        if status is not None:
            rows[1][2] = _status_summary(status)

        if args.write is not None:
            wanted = parse_mode(args.write)
            original = run("read mode (before write)",
                           lambda: session.read_noise_mode(timeout=args.timeout))
            run(f"set mode 0x{wanted:02X} ({noise_name(wanted)})",
                lambda: session.set_noise_mode(wanted, timeout=args.timeout))
            after = run("read mode (after write)",
                        lambda: session.read_noise_mode(timeout=args.timeout))
            if after is not None and rows[-2][1] == "PASS" and after != wanted:
                rows[-1][1] = "FAIL"
                rows[-1][2] = f"expected 0x{wanted:02X}, read back 0x{after:02X}"
            if original is not None and original != wanted:
                run(f"restore mode 0x{original:02X}",
                    lambda: session.set_noise_mode(original, timeout=args.timeout))
    finally:
        transport.close()

    failed = [row for row in rows if row[1] == "FAIL"]
    if args.json:
        print(json.dumps([{"step": label, "status": state, "detail": str(detail)}
                          for label, state, detail in rows], indent=2))
    else:
        print(f"live smoke test: {args.address or 'replay'}")
        for label, state, detail in rows:
            print(f"  [{state:<4}] {label:<32} {detail}")
        print("RESULT:", "FAIL" if failed else "OK")
    return 1 if failed else 0


def cmd_send(args) -> int:
    cmd = parse_cmd(args.command)
    if cmd in DANGEROUS and not args.force:
        raise SystemExit(
            f"0x{cmd:04X} ({DANGEROUS[cmd]}) is part of the hearing/ear-scan family "
            "and may play loud tones; refusing. Pass --force to override.")
    payload = bytes.fromhex(args.payload) if args.payload else b""
    transport, session = open_session(args)
    try:
        frames = session.send(cmd, payload, args.type)
        packets = session.listen(args.read) if args.read > 0 else []
        if args.json:
            print(json.dumps({"cmd": cmd, "payload": payload.hex(),
                              "frames": [f.hex() for f in frames],
                              "replies": [packet_dict(p) for p in packets]}, indent=2))
        else:
            print(f"tx 0x{cmd:04X} {t.CLASS_NAMES.get(t.cmd_class(cmd), '?'):<24} "
                  f"[{payload.hex(' ')}]  ({len(frames)} frame(s))")
            for packet in packets:
                print_packet(packet)
        return 0
    finally:
        transport.close()


# --------------------------------------------------------------------------
# argument parser
# --------------------------------------------------------------------------
#: Defaults for the shared options. ``SUPPRESS`` in the parsers means an option
#: is only recorded where it is actually given, so it can appear either before
#: or after the subcommand without the subparser's defaults clobbering it.
_DEFAULTS = {
    "address": None, "channel": None, "uuid": None, "transport": "auto",
    "replay": None, "loose": False, "frame_size": 512, "interval_ms": 10,
    "timeout": 1.5, "json": False, "reset": False,
}


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--address", default=argparse.SUPPRESS,
                        help="device address, e.g. AA-BB-CC-DD-EE-FF")
    parser.add_argument("--channel", type=int, default=argparse.SUPPRESS,
                        help="RFCOMM channel (default: discover SPP)")
    parser.add_argument("--uuid", default=argparse.SUPPRESS,
                        help=f"service UUID to discover (default {SPP_UUID})")
    parser.add_argument("--transport", choices=["auto", "macos", "replay"],
                        default=argparse.SUPPRESS)
    parser.add_argument("--replay", metavar="FILE", default=argparse.SUPPRESS,
                        help="run against a recorded fixture")
    parser.add_argument("--reset", action="store_true", default=argparse.SUPPRESS,
                        help="live only: drop and re-establish the ACL link first")
    parser.add_argument("--loose", action="store_true", default=argparse.SUPPRESS,
                        help="with --replay, do not require writes to match the recording")
    parser.add_argument("--frame-size", type=int, default=argparse.SUPPRESS,
                        help="transport MTU (default 512)")
    parser.add_argument("--interval-ms", type=int, default=argparse.SUPPRESS,
                        help="pacing between frames")
    parser.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="read timeout in seconds")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common)

    ap = argparse.ArgumentParser(prog="tlcli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("services", help="list SDP services (macOS)", parents=[common])
    p.set_defaults(func=cmd_services)

    p = sub.add_parser("caps", help="read the capability bitmap", parents=[common])
    p.set_defaults(func=cmd_caps)

    p = sub.add_parser("info", parents=[common],
                       help="read model, firmware, battery, wear, mode, use time")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("backup", parents=[common],
                       help="capture the device's user settings into JSON")
    p.add_argument("file", help="output file, or '-' for stdout")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", parents=[common],
                       help="re-apply user settings from a backup file")
    p.add_argument("file", help="backup file to read")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be applied without writing")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("mode", help="get or set the active noise mode", parents=[common])
    p.add_argument("mode_args", nargs="*", metavar="VALUE",
                   help="'get' to read, 'set <value>' / '<value>' to write, "
                        "'cycle' to test all six")
    p.add_argument("--dwell", type=float, default=5.0, metavar="SECONDS",
                   help="with 'cycle': seconds to hold each mode (default 5)")
    p.add_argument("--say", action="store_true",
                   help="with 'cycle': speak each mode name via macOS 'say'")
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("smoke", parents=[common],
                       help="end-to-end live check: connect, negotiate, poll, optional write")
    p.add_argument("--write", nargs="?", const="anc", default=None, metavar="MODE",
                   help="also write a noise mode, read it back, and restore (default: anc)")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("send", help="send a raw command and listen for replies", parents=[common])
    p.add_argument("command", help="command id, e.g. 0x0404")
    p.add_argument("payload", nargs="?", help="payload bytes as hex, e.g. '01 01 20'")
    p.add_argument("--type", type=int, default=0, help="type/flags byte (default 0)")
    p.add_argument("--read", type=float, default=1.0, help="seconds to listen (default 1.0)")
    p.add_argument("--force", action="store_true", help="allow hearing/ear-scan commands")
    p.set_defaults(func=cmd_send)

    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    for key, value in _DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    try:
        return args.func(args)
    except t.NotWorn as exc:
        print(f"not worn: {exc}", file=sys.stderr)
        print("put the earbuds in and try again.", file=sys.stderr)
        return 2
    except t.Timeout as exc:
        print(f"timeout: {exc}", file=sys.stderr)
        return 3
    except (t.TLError, ReplayError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
