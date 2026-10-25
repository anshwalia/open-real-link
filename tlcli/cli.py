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

#: The families ``send`` refuses unless ``--force`` is given.  The table itself
#: lives in ``tlcore`` (:data:`tlcore.DANGEROUS_COMMANDS`) so that every front end
#: shares one guard; this is just the local name for it.
DANGEROUS = t.DANGEROUS_COMMANDS

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


def cmd_eq(args) -> int:
    """Read the active sound-effect/EQ mode plus the EQ list, or write the mode."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        eq_args = list(args.eq_args)
        action = eq_args[0] if eq_args else "get"
        if action in ("get", "read"):
            mode = session.read_eq_mode()
            info = session.read_eq_info()
            if args.json:
                print(json.dumps({"mode": mode, "eq_info": info}, indent=2, default=str))
                return 0
            print(f"eq mode   : {mode}")
            for entry in info["entries"]:
                name = f'  "{entry["name"]}"' if entry["name"] else ""
                print(f"  {'*' if entry['is_selected'] else ' '} eqId={entry['eq_id']}"
                      f"  min={entry['min']} max={entry['max']}"
                      f"  bands={len(entry['points'])}{name}")
            if not info["entries"]:
                print("  (no EQ entries)")
            return 0
        if action in ("set-mode", "set"):
            if len(eq_args) < 2:
                raise SystemExit("usage: eq set-mode <0..6>")
            value = int(eq_args[1], 0)
            session.set_eq_mode(value)
            mode = session.read_eq_mode()
            print(f"eq mode   : wrote {value}, read back {mode}"
                  + ("" if mode == value else "  (MISMATCH)"))
            return 0
        raise SystemExit(f"unknown eq action: {action!r} (use 'get' or 'set-mode <n>')")
    finally:
        transport.close()


def cmd_high_volume(args) -> int:
    """Read or write the high-volume (loudness boost) setting."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        hv_args = list(args.hv_args)
        action = (hv_args[0] if hv_args else "get").lower()
        if action in ("get", "read"):
            enabled = session.read_high_volume()
            if args.json:
                print(json.dumps({"high_volume": enabled}, indent=2))
            else:
                print(f"high volume: {'on' if enabled else 'off'}")
            return 0
        if action in ("on", "off", "set"):
            if action == "set":
                if len(hv_args) < 2:
                    raise SystemExit("usage: high-volume set <on|off>")
                word = hv_args[1].lower()
            else:
                word = action
            if word not in ("on", "off"):
                raise SystemExit(f"expected 'on' or 'off', got {word!r}")
            wanted = word == "on"
            session.set_high_volume(wanted)
            enabled = session.read_high_volume()
            if args.json:
                print(json.dumps({"requested": wanted, "high_volume": enabled}, indent=2))
            else:
                print(f"high volume: wrote {'on' if wanted else 'off'}, "
                      f"read back {'on' if enabled else 'off'}"
                      + ("" if enabled == wanted else "  (MISMATCH)"))
            return 0
        raise SystemExit(f"unknown high-volume action: {action!r} "
                         "(use 'get', 'on', 'off', or 'set on|off')")
    finally:
        transport.close()


def cmd_find(args) -> int:
    """Ring the buds to locate them (``0x0400``), then stop.

    There is no read pair, so the only confirmation is the ``0x8400`` ack.  The
    stop is sent from a ``finally``: a lost ack, a keyboard interrupt or any
    other error still stops the chime rather than leaving the buds ringing.
    """
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        if not session.supports(t.CMD_SET_FIND_MODE):
            raise SystemExit("the device does not advertise find mode "
                             "(capability bit 5); nothing to do")
        if args.stop:
            session.set_find_mode(False)
            print("find mode: off")
            return 0
        session.set_find_mode(True)
        seconds = max(0.0, args.seconds)
        if not args.json:
            print(f"find mode: ON -- the buds are ringing; "
                  f"stopping in {seconds:g}s (Ctrl-C also stops)")
        try:
            time.sleep(seconds)
        finally:
            session.set_find_mode(False)
        if args.json:
            print(json.dumps({"find_mode": True, "rang_seconds": seconds,
                              "stopped": True}, indent=2))
        else:
            print("find mode: off")
        return 0
    finally:
        transport.close()


def cmd_ai_status(args) -> int:
    """Read or write the AI / translation status (``0x0131`` / ``0x0428``).

    **Verified live** on the Air7 Pro (2026-10-20): ``state=1`` activates the
    session for ``type`` and the ``0x0131`` read-back follows; ``state=0``
    deactivates only with the matching active ``type``.
    """
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        ai_args = list(args.ai_args)
        action = (ai_args[0] if ai_args else "get").lower()
        if action in ("get", "read"):
            status = session.read_ai_status()
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                print(f"ai status : state={status['state']} type={status['type']}")
            return 0
        if action == "set":
            if len(ai_args) < 3:
                raise SystemExit("usage: ai-status set <state 0|1> <type 0|1|2|4>")
            state, ai_type = int(ai_args[1], 0), int(ai_args[2], 0)
            session.set_ai_status(state, ai_type)
            after = session.read_ai_status()
            if args.json:
                print(json.dumps({"requested": {"state": state, "type": ai_type},
                                  "ai_status": after}, indent=2))
            else:
                print(f"ai status : wrote state={state} type={ai_type}, "
                      f"read back state={after['state']} type={after['type']}"
                      + ("" if after["state"] == state else "  (MISMATCH)"))
            return 0
        raise SystemExit(f"unknown ai-status action: {action!r} "
                         "(use 'get' or 'set <state> <type>')")
    finally:
        transport.close()


def cmd_multipoint(args) -> int:
    """Read the paired-device / multipoint list (``0x0112``)."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        result = session.read_multi_connect()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"multipoint: {result['count']} device(s)")
            for entry in result["entries"]:
                print(f"  {entry['mac']}  state={entry['state']} "
                      f"flag={entry['flag']}  {entry['name']}")
        return 0
    finally:
        transport.close()


def cmd_bass(args) -> int:
    """Read the multi-band bass engine (``0x012C``)."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        result = session.read_multi_bass_engine()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"bass engine: {result['count']} band(s)")
            for entry in result["entries"]:
                band_type = entry["type"]
                label = f"type {band_type}" if band_type is not None else "no type"
                print(f"  {label:9} range {entry['min']:+d}..{entry['max']:+d} "
                      f"current {entry['current']:+d}")
        return 0
    finally:
        transport.close()


def cmd_personalized_anc(args) -> int:
    """Read the personalized-noise calibration result *exists* flag (``0x011A``)."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        result = session.read_personalized_anc()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"personalized noise : result stored={bool(result['exists'])} "
                  f"(exists={result['exists']})")
        return 0
    finally:
        transport.close()


def cmd_notification_caps(args) -> int:
    """Read the push-event types the device supports (``0x0200``)."""
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        result = session.read_notification_capabilities()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            names = ", ".join(f"0x{t:02X}" for t in result["types"])
            print(f"notification capabilities: {result['count']} type(s): {names or '-'}")
        return 0
    finally:
        transport.close()


def cmd_identity(args) -> int:
    """Read the identity/status fields (``0x0102``/``0x0104``/``0x0107``/``0x010B``).

    All four are ungated or always-on reads.  **Verified live** on the Air7 Pro
    (2026-10-20): vendor id ``0x072F``, protocol version ``000001``, colour id
    ``6``, and one OTA-capability record (``device_type=1, support_ota=True,
    ota_type=1``).
    """
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        result = {
            "vendor_id": session.read_remote_vendor_id(),
            "protocol_version": session.read_protocol_version(),
            "upgrade_capability": session.read_upgrade_capability(),
            "color_id": session.read_color_id(),
        }
    finally:
        transport.close()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    vendor = result["vendor_id"]["vendor_id"]
    print(f"vendor id : 0x{vendor:04X}" if vendor is not None else "vendor id : unknown")
    print(f"protocol  : {result['protocol_version']['data'] or '-'}")
    print(f"color id  : {result['color_id']['color_id']}")
    entries = result["upgrade_capability"]["entries"]
    print("upgrade   : " + (", ".join(
        f"type={e['device_type']} ota={e['support_ota']}/{e['ota_type']}"
        for e in entries) or "-"))
    return 0


def cmd_compactness(args) -> int:
    """Drive in-ear detection (``0x0405``) and decode the ``0x0204`` event-``0x04`` push.

    The trigger has no read-back pair: the firmware answers with two
    ``[earSide][value]`` records on the ``0x0204`` event-``0x04`` notification, so
    the result is observed by listening after the write.
    """
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities()
        cp_args = list(args.cp_args)
        action = (cp_args[0] if cp_args else "get").lower()
        if action in ("start", "on"):
            session.start_compactness_detect()
        elif action in ("stop", "off"):
            session.stop_compactness_detect()
        elif action not in ("get", "read", "listen"):
            raise SystemExit(f"unknown compactness action: {action!r} "
                             "(use 'get', 'start', or 'stop')")
        seconds = args.seconds
        packets = session.listen(seconds)
    finally:
        transport.close()
    rows = [t.decode_event(p) for p in packets]
    if args.json:
        print(json.dumps({"action": action, "events": rows}, indent=2, default=str))
        return 0
    print(f"compactness {action}: listened {seconds:g}s, {len(rows)} packet(s)")
    for row in rows:
        _print_event(row)
    return 0


# --------------------------------------------------------------------------
# keys / gestures
# --------------------------------------------------------------------------
def _lookup_name(names: dict, text: str, what: str) -> int:
    """Resolve ``text`` as an int (``0x`` allowed) or a label from ``names``."""
    try:
        return int(text, 0)
    except ValueError:
        pass
    wanted = text.strip().lower().replace("_", "-")
    for value, name in names.items():
        if name == wanted:
            return value
    raise SystemExit(f"unknown {what} {text!r}; use a number or one of "
                     + ", ".join(sorted(names.values())))


def parse_key_update(spec: str) -> tuple[tuple[int, int, int], int]:
    """Parse ``DEVICE:BUTTON:ACTION:FUNCTION`` -> ``((device, button, action), function)``.

    Each field is an integer (``0x`` allowed) or, where a label exists, a name
    such as ``right``, ``long-press`` or ``noise-control``.
    """
    parts = spec.replace(":", ",").split(",")
    if len(parts) != 4:
        raise SystemExit(f"want DEVICE:BUTTON:ACTION:FUNCTION (got {spec!r})")
    device = _lookup_name(t.DEVICE_TYPE_NAMES, parts[0], "device")
    button = _lookup_name({}, parts[1], "button")
    action = _lookup_name(t.ACTION_NAMES, parts[2], "action")
    function = _lookup_name(t.FUNCTION_NAMES, parts[3], "function")
    return (device, button, action), function


def _key_table_dict(table: dict) -> dict:
    return {
        "status": table["status"],
        "count": table["count"],
        "entries": [
            {"device_type": e.device_type, "button": e.button, "action": e.action,
             "function": e.function, "describe": e.describe()}
            for e in table["entries"]
        ],
    }


def print_key_table(table: dict) -> None:
    print(f"key functions: status={table['status']} count={table['count']}")
    for entry in table["entries"]:
        print(f"  {entry.device_type}:{entry.button}:{entry.action}:{entry.function}"
              f"   {entry.describe()}")


def cmd_keys(args) -> int:
    """Read the gesture table, or write it back patched / with long press off."""
    tokens = list(args.key_args)
    action = tokens[0].lower() if tokens else "get"
    if action in ("get", "read"):
        return _keys_get(args)
    if action in ("set", "disable-long-press"):
        return _keys_write(args, action, tokens[1:])
    raise SystemExit(f"unknown keys action {action!r} (use 'get', "
                     "'set DEVICE:BUTTON:ACTION:FUNCTION', or 'disable-long-press')")


def _keys_get(args) -> int:
    transport, session = open_session(args)
    try:
        session.negotiate_capabilities(timeout=args.timeout)
        if not session.supports(t.CMD_GET_KEY_FUNCTION):
            raise SystemExit("device does not advertise the key-function table (0x0108)")
        table = session.read_key_function(timeout=args.timeout)
    finally:
        transport.close()
    if args.json:
        print(json.dumps(_key_table_dict(table), indent=2))
    else:
        print_key_table(table)
    return 0


def _keys_write(args, action: str, specs: list[str]) -> int:
    updates: dict = {}
    for spec in specs:
        key, function = parse_key_update(spec)
        updates[key] = function
    if action == "set" and not updates:
        raise SystemExit("keys set needs at least one DEVICE:BUTTON:ACTION:FUNCTION")

    transport, session = open_session(args)
    try:
        session.negotiate_capabilities(timeout=args.timeout)
        if not session.supports(t.CMD_SET_KEY_FUNCTION):
            raise SystemExit("device does not advertise setKeyFunction (0x0401)")
        table = session.read_key_function(timeout=args.timeout)
        if not table["entries"]:
            raise SystemExit("device returned an empty key-function table; refusing to write")
        if action == "set":
            present = {(e.device_type, e.button, e.action) for e in table["entries"]}
            missing = [key for key in updates if key not in present]
            if missing:
                raise SystemExit("no such gesture in the device's table: "
                                 + ", ".join(f"{d}:{b}:{a}" for d, b, a in missing))
            entries = t.patch_key_functions(table["entries"], updates)
        else:
            entries = t.disable_long_press(table["entries"])
        changes = [{"device_type": old.device_type, "button": old.button,
                    "action": old.action, "from": old.function, "to": new.function}
                   for old, new in zip(table["entries"], entries) if old != new]
        if args.save:
            pathlib.Path(args.save).write_text(
                json.dumps(_key_table_dict(table), indent=2) + "\n", encoding="utf-8")
            print(f"saved the original table to {args.save}")
        if not args.confirm:
            if args.json:
                print(json.dumps({"dry_run": True, "changes": changes}, indent=2))
            else:
                print("dry run: pass --confirm to write the new table")
                for change in changes:
                    print(f"  {change['device_type']}:{change['button']}:{change['action']}: "
                          f"{change['from']} -> {change['to']}")
            return 0
        if not changes:
            print("no change needed")
            return 0
        status = session.set_key_function(entries, timeout=args.timeout)
        after = session.read_key_function(timeout=args.timeout)
    finally:
        transport.close()
    if args.json:
        print(json.dumps({"status": status, "changes": changes,
                          "entries": _key_table_dict(after)["entries"]}, indent=2))
        return 0
    print(f"wrote {len(entries)} entries; 0x8401 status={status}")
    print_key_table(after)
    return 0


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
def decode_event(packet: dict) -> dict:
    """Decode one pushed packet into a serializable dict."""
    cmd = packet["cmd"]
    payload = packet["payload"]
    out = {"cmd": f"0x{cmd:04X}", "cmd_int": cmd,
           "name": t.CMD_NAMES.get(cmd, ""), "payload": payload.hex()}
    if cmd == t.CMD_NOTIFICATION_EVENT:
        event = t.parse_notification_event(payload)
        out["event"] = event["event_name"]
        out["event_int"] = event["event"]
        if event["body"]:
            out["body"] = event["body"].hex()
        if event["tlv"]:
            out["tlv"] = event["tlv"]
        if event["event"] == t.EVENT_USER_INTERACTION:
            press = t.parse_user_interaction(event["body"])
            if press:
                out["press"] = press
        if "compactness" in event:
            out["compactness"] = event["compactness"]
        if "multi_connect" in event:
            out["multi_connect"] = event["multi_connect"]
    elif cmd in t.REQUEST_DECODERS:
        out["request"] = t.decode_request(cmd, payload)
    else:
        state = t.decode_state(cmd, payload)
        if state is not None:
            out["state"] = state
    return out


def _print_event(row: dict) -> None:
    head = f"  0x{row['cmd_int']:04X}  {row['name'] or '?'}"
    if "press" in row:
        press = row["press"]
        print(f"{head}  button press: {press['function_name']}"
              f"  (dev={press['device_type']} btn={press['button']}"
              f" action={press['button_action']} scene={press['scene']})")
    elif "compactness" in row:
        parts = ", ".join(f"{r['side_name']}={r['value']}" for r in row["compactness"])
        print(f"{head}  compactness: {parts}")
    elif "multi_connect" in row:
        mc = row["multi_connect"]
        print(f"{head}  multiConnect: {mc['count']} device(s)")
        for entry in mc["entries"]:
            print(f"        {entry['mac']}  state={entry['state']} "
                  f"flag={entry['flag']}  {entry['name']!r}")
    elif "public_event" in row:
        public = row["public_event"]
        detail = []
        if public["volume"] is not None:
            detail.append(f"volume={public['volume']}")
        if public["ai"] is not None:
            detail.append(f"ai={public['ai']['state']}/{public['ai']['type']}")
        print(f"{head}  publicEvent: flags=0x{public['flags']:04X} "
              + (" ".join(detail) or "(no flag set)"))
    elif "event" in row:
        print(f"{head}  event={row['event']}  body={row.get('body', '')}")
    elif "request" in row:
        print(f"{head}  request: {row['request']}")
    elif "state" in row:
        print(f"{head}  {row['state']}")
    else:
        print(f"{head}  [{row['payload']}]")


def cmd_events(args) -> int:
    """Listen for pushes and decode them (button presses, battery, requests)."""
    transport, session = open_session(args)
    try:
        try:
            session.negotiate_capabilities(timeout=args.timeout)
        except t.TLError:
            pass
        packets = list(session.events) + session.listen(args.seconds)
    finally:
        transport.close()
    rows = [t.decode_event(p) for p in packets]
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    print(f"listened {args.seconds:g}s: {len(rows)} packet(s)")
    for row in rows:
        _print_event(row)
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
    pathlib.Path(args.file).write_text(text + "\n", encoding="utf-8")
    print(f"backup written to {args.file}")
    print(f"  device    : {snapshot['device']}")
    print(f"  captured  : {len(snapshot['raw'])} responses")
    print(f"  restorable: {', '.join(sorted(snapshot['settings'])) or 'none'}")
    if snapshot["skipped"]:
        print(f"  skipped   : {len(snapshot['skipped'])}")
    return 0


def cmd_restore(args) -> int:
    """Re-apply the restorable user settings from a backup snapshot."""
    data = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
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


def say_command(text: str, *, voice, rate, out) -> list[str]:
    """Build the macOS ``say`` argv for ``text``. Pure; used by the tests."""
    cmd = ["say"]
    if out:
        cmd += ["-o", out]
    if voice:
        cmd += ["-v", voice]
    if rate:
        cmd += ["-r", str(int(rate))]
    cmd.append(text)
    return cmd


def say_text(text: str, *, voice=None, rate=None,
             tail_silence_ms: int = SAY_TAIL_SILENCE_MS) -> None:
    """Speak ``text`` through the current audio output (macOS ``say``).

    This is **host-side**: the TL protocol has no text-to-speech command, so the
    headset is used as an ordinary audio sink. Set the headset as the macOS
    output device first. The trailing ``[[slnc]]`` keeps the last phoneme from
    being clipped when the Bluetooth buffer tears down.
    """
    if sys.platform != "darwin":
        raise SystemExit("speaking needs macOS 'say'; on other systems play the text "
                         "with your own TTS engine to the headset's audio output")
    cue = f"{text} [[slnc {tail_silence_ms}]]" if tail_silence_ms else text
    subprocess.run(say_command(cue, voice=voice, rate=rate, out=None), check=False)


def announce(text: str) -> None:
    """Speak a short mode cue; silently no-op off macOS (used by ``mode cycle``)."""
    if sys.platform != "darwin":
        return
    try:
        say_text(text)
    except OSError:
        pass


def _seconds_phrase(seconds: int) -> str:
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def _battery_phrase(devices: dict) -> str:
    parts = []
    for key, label in (("L", "left"), ("R", "right")):
        reading = devices.get(key)
        if reading:
            parts.append(f"{reading['level']} percent {label}")
    case = devices.get("CASE")
    if case and case["level"]:
        parts.append(f"case {case['level']} percent")
    if not parts:
        return ""
    return " and ".join(parts) if len(parts) == 2 else ", ".join(parts)


def status_sentence(status: dict) -> str:
    """Compose a short spoken summary from a ``Session.read_status()`` dict.

    Pure. Pieces the device did not report are skipped rather than spoken as
    noise; this is the only caller of ``say --status``.
    """
    model = (status.get("model") or {}).get("model_id")
    if model is None:
        name = "the headset"
    else:
        spec = t.device_spec(f"{model:06X}")
        name = spec.name if spec else f"model 0x{model:06X}"
    clauses = [name]
    firmware = (status.get("firmware") or {}).get("versions") or []
    if firmware:
        clauses.append(f"firmware {firmware[0]}")
    battery = _battery_phrase((status.get("battery") or {}).get("devices") or {})
    if battery:
        clauses.append(f"battery {battery}")
    wear = (status.get("wear") or {}).get("devices") or {}
    in_ear = [d for d in ("L", "R") if wear.get(d) == t.WEAR_IN_EAR]
    if len(in_ear) == 2:
        clauses.append("in ear")
    elif in_ear:
        clauses.append(f"only the {'left' if in_ear[0] == 'L' else 'right'} earbud in ear")
    elif wear:
        clauses.append("not worn")
    mode = status.get("noise_mode")
    if mode is not None:
        spoken = t.NOISE_MODE_NAMES.get(mode, f"0x{mode:02X}").split(" (")[0]
        clauses.append(f"noise mode {spoken.lower()}")
    use = (status.get("use_time") or {}).get("devices") or {}
    use_parts = [f"{_seconds_phrase(use[d])} {label}"
                 for d, label in (("L", "left"), ("R", "right")) if use.get(d)]
    if use_parts:
        clauses.append("use time " + " and ".join(use_parts))
    return ", ".join(clauses) + "."


def cmd_say(args) -> int:
    """Speak text or a live status summary through the headset (host-side TTS)."""
    # Every path out of this command needs the macOS ``say`` binary -- to list
    # voices, to synthesise a file, or to speak -- so it is checked once, up
    # front.  Without this, ``--list-voices`` off macOS reported an OSError
    # about a missing executable instead of naming the platform.
    if sys.platform != "darwin":
        raise SystemExit(
            "macOS-only: 'say' is the synthesis engine; on other systems play "
            "the text with your own TTS engine to the headset's audio output")
    if args.list_voices:
        try:
            listing = subprocess.run(["say", "-v", "?"], capture_output=True,
                                     text=True, check=False)
        except OSError as exc:
            raise SystemExit(f"cannot run 'say': {exc}") from exc
        print(listing.stdout.rstrip())
        return 0
    if args.status:
        if args.text:
            raise SystemExit("give either text or --status, not both")
        transport, session = open_session(args)
        try:
            session.negotiate_capabilities(timeout=args.timeout)
            status = session.read_status(timeout=args.timeout)
        finally:
            transport.close()
        text = status_sentence(status)
        print(text)
    else:
        text = " ".join(args.text).strip()
        if not text and not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        if not text:
            raise SystemExit("nothing to say; pass text, --status, or pipe it in")
    if args.out:
        subprocess.run(say_command(text, voice=args.voice, rate=args.rate, out=args.out),
                       check=False)
        print(f"wrote {args.out} ({len(text)} chars)")
        return 0
    repeat = max(1, args.repeat)
    for i in range(repeat):
        if repeat > 1:
            print(f"speaking {i + 1}/{repeat}", file=sys.stderr)
        say_text(text, voice=args.voice, rate=args.rate)
    return 0


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
        name = t.CMD_NAMES.get(cmd, "")
        label = f" ({name})" if name else ""
        raise SystemExit(
            f"0x{cmd:04X}{label} is part of the {DANGEROUS[cmd]}, which is "
            "deliberately not implemented; refusing. Pass --force to override.")
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
    parser.add_argument("--transport", choices=["auto", "macos", "windows", "replay"],
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

    p = sub.add_parser("eq", parents=[common],
                       help="read the sound-effect/EQ mode and preset list")
    p.add_argument("eq_args", nargs="*", metavar="VALUE",
                   help="'get' (default) to read, or 'set-mode <0..6>' to write")
    p.set_defaults(func=cmd_eq)

    p = sub.add_parser("high-volume", parents=[common],
                       help="read or write the high-volume (loudness boost) setting")
    p.add_argument("hv_args", nargs="*", metavar="ACTION",
                   help="'get' (default), 'on', 'off', or 'set on|off'")
    p.set_defaults(func=cmd_high_volume)

    p = sub.add_parser("find", parents=[common],
                       help="ring the buds so you can locate them (0x0400), then stop")
    p.add_argument("--seconds", type=float, default=10.0, metavar="N",
                   help="how long to ring before stopping (default 10; 0 rings and stops at once)")
    p.add_argument("--stop", action="store_true",
                   help="only send the stop (0x0400 00), e.g. after an interrupted run")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("ai-status", parents=[common],
                       help="read or write the AI / translation status (0x0131 / 0x0428)")
    p.add_argument("ai_args", nargs="*", metavar="ACTION",
                   help="'get' (default), or 'set <state 0|1> <type 0|1|2|4>'")
    p.set_defaults(func=cmd_ai_status)

    p = sub.add_parser("multipoint", parents=[common],
                       help="read the paired-device / multipoint list (0x0112)")
    p.set_defaults(func=cmd_multipoint)

    p = sub.add_parser("personalized-noise", parents=[common],
                       help="read the personalized-noise result exists flag (0x011A)")
    p.set_defaults(func=cmd_personalized_anc)

    p = sub.add_parser("bass", parents=[common],
                       help="read the multi-band bass engine (0x012C)")
    p.set_defaults(func=cmd_bass)

    p = sub.add_parser("notification-caps", parents=[common],
                       help="read the push-event types the device supports (0x0200)")
    p.set_defaults(func=cmd_notification_caps)

    p = sub.add_parser("identity", parents=[common],
                       help="read the identity/status fields (0x0102/0x0104/0x0107/0x010B)")
    p.set_defaults(func=cmd_identity)

    p = sub.add_parser("compactness", parents=[common],
                       help="drive in-ear detection (0x0405) and read the event-0x04 push")
    p.add_argument("cp_args", nargs="*", metavar="ACTION",
                   help="'get' (default, just listen), 'start', or 'stop'")
    p.add_argument("--seconds", type=float, default=4.0, metavar="N",
                   help="seconds to listen for the event push (default 4)")
    p.set_defaults(func=cmd_compactness)

    p = sub.add_parser("keys", parents=[common],
                       help="read or write the key-function/gesture table")
    p.add_argument("key_args", nargs="*", metavar="ACTION",
                   help="'get' (default), 'set DEVICE:BUTTON:ACTION:FUNCTION ...', "
                        "or 'disable-long-press'")
    p.add_argument("--save", metavar="JSON",
                   help="save the original table before writing")
    p.add_argument("--confirm", action="store_true", help="required to write")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("events", parents=[common],
                       help="listen for device pushes and decode them")
    p.add_argument("--seconds", type=float, default=10.0, metavar="N",
                   help="seconds to listen (default 10)")
    p.set_defaults(func=cmd_events)

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
    p.add_argument("--force", action="store_true",
                   help="allow the blocked families (tone, OTA, debug)")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("say", parents=[common],
                       help="speak text through the headset (host-side macOS TTS)")
    p.add_argument("text", nargs="*", help="words to speak; read stdin if omitted")
    p.add_argument("--voice", metavar="NAME", help="macOS voice (see --list-voices)")
    p.add_argument("--rate", type=float, metavar="WPM", help="words per minute")
    p.add_argument("--repeat", type=int, default=1, metavar="N",
                   help="speak it N times (default 1)")
    p.add_argument("--out", metavar="FILE", help="write AIFF to FILE instead of playing")
    p.add_argument("--list-voices", action="store_true",
                   help="list installed voices and exit")
    p.add_argument("--status", action="store_true",
                   help="read the live device and speak a status summary")
    p.set_defaults(func=cmd_say)

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
