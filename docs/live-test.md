# Live test with hardware

How to prove the controller against a real headset. The offline suite covers the
protocol, transport contract, session, and CLI against recorded fixtures; this
runbook covers the one thing it cannot: the macOS RFCOMM transport actually
moving bytes.

`tltransport/macos.py` is lifted from tooling that was validated on hardware.
`MacOSTransport` as it exists in this repository was first run end to end this
way on **2026-10-18** against a Buds Air7 Pro: the read-only `smoke` reported
49 commands, model `0x064C12`, firmware `1.1.0.85`, mode `0x01`, and `wear=in-ear`
(see §5). The first open attempt was refused with `0xE00002BC`; `--reset`
cleared it. Re-run this to check any change to the transport.

---

## 1. Prerequisites

| | |
| --- | --- |
| Host | macOS with a working Bluetooth radio |
| Python | 3.9+ |
| Dependency | `pyobjc-framework-IOBluetooth` (`pip install ".[macos]"`) |
| Device | a realme/OPPO TL headset **already paired** to this Mac |

Set up the repository virtualenv first — every `python` below means the one in
`.venv` (see [`../AGENTS.md`](../AGENTS.md)):

```console
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e ".[dev,macos,gui]"
```

Check the dependency and that the headset is paired (read-only):

```console
$ python -c "import IOBluetooth; print([d.getName() for d in IOBluetooth.IOBluetoothDevice.pairedDevices() or []])"
['<your headset name>', ...]
```

The device address looks like `AA-BB-CC-DD-EE-FF` (macOS may show lower case and
hyphens). Do **not** commit your own address into the repository.

## 2. Safety — read this before running anything that writes

* **Read-only by default.** `tlcli smoke`, `info`, `caps`, and `mode get` only
  poll. They are safe.
* **The write path is gated on the buds being worn.** `mode set` and
  `smoke --write` are accepted only while the earbuds are in an ear; otherwise
  the device answers `0x8404 0e` and the CLI reports *not worn* and exits `2`.
* **Never sweep command space.** The hearing-test / ear-scan family
  (`0x011E`, `0x011F`, `0x040E`, `0x0415`) drives **loud, repeated tones** in the
  wearer's ears. The CLI refuses those unless `--force` is given; do not pass it
  on a worn headset.
* **The OTA and debug families are refused too.** `send` blocks
  `0x0300`–`0x0308` (firmware push, no read-back) and `0x0F00`–`0x0F02` (debug
  log) unless `--force` is given. Both are deliberately unimplemented; there is
  no reason to override this.
* `send` will otherwise transmit an arbitrary command id. Prefer the named
  subcommands.

## 3. Run it

Set your device address once for the session:

```console
$ export BUDS=AA-BB-CC-DD-EE-FF
```

### Step 1 — can the host see the device's services? (no bytes sent)

```console
$ python -m tlcli --address "$BUDS" services
SDP services on AA-BB-CC-DD-EE-FF:
  ...
  record[ 7]  channel=15  0000079A-D102-11E1-9B23-00025B00A5A5  (OPPO interaction)
  record[ 8]  channel=17  DF21FE2C-2515-4FDB-8886-F12C4D67927C
  record[10]  channel=12  00001101-0000-1000-8000-00805F9B34FB  (SPP (Serial Port))
  ...
```

Channel **12** is the SPP channel the vendor app uses; discovery finds it for you
(the record order and indices vary between units).

### Step 2 — the end-to-end read-only check

```console
$ python -m tlcli --address "$BUDS" smoke
live smoke test: AA-BB-CC-DD-EE-FF
  [PASS] capability bitmap (0x8100)       49 commands, status=0
  [PASS] status poll (0x0101..0x0128)     model=0x064C12 fw=1.1.0.85 mode=0x01(Normal (noise off)) battery[L=100% R=100% CASE=0%] wear=in-ear
RESULT: OK
```

Expected on a Buds Air7 Pro: **49 supported commands**, model `0x064C12`,
firmware `1.1.0.85`. Battery and wear vary with the actual state. If channel 12
is refused (`0xE00002BC`), add `--reset`.

### Step 3 — read the noise mode

```console
$ python -m tlcli --address "$BUDS" mode get
noise mode: 0x01  Normal (noise off)
```

### Step 4 — the write, with the buds **worn**

```console
$ python -m tlcli --address "$BUDS" mode set anc
set 0x20 (ANC smart); device now 0x20 (ANC smart)
```

Then confirm by ear. `smoke --write` does the same and **restores the original
mode afterwards**:

```console
$ python -m tlcli --address "$BUDS" smoke --write anc
  ...
  [PASS] set mode 0x20 (ANC smart)
  [PASS] read mode (after write)          0x20
  [PASS] restore mode 0x01
RESULT: OK
```

`mode cycle` is the definitive write test: it applies **all six** modes, reads
each one back, verifies the round trip, and restores the original mode:

```console
$ python -m tlcli --address "$BUDS" --reset mode cycle
noise mode cycle (starting 0x01 Normal (noise off))
  [PASS] 0x01 Normal (noise off)   -> 0x01 (Normal (noise off))
  [PASS] 0x02 Transparency         -> 0x02 (Transparency)
  [PASS] 0x04 ANC light            -> 0x04 (ANC light)
  [PASS] 0x08 ANC deep             -> 0x08 (ANC deep)
  [PASS] 0x10 ANC medium           -> 0x10 (ANC medium)
  [PASS] 0x20 ANC smart            -> 0x20 (ANC smart)
restored 0x01 (Normal (noise off))
RESULT: OK
```

Each mode is held for **5 seconds** by default so you can hear the change
(`--dwell SECONDS` overrides it; use `--dwell 0` for a fast, silent check). Add
`--say` to also have macOS speak each mode name out loud as it changes (a
voice-over for by-ear checks). The cycle always starts at Normal and restores
the original mode at the end.

### Step 5 — a raw command, if you want to see the bytes

```console
$ python -m tlcli --address "$BUDS" send 0x010C 01 --read 1
tx 0x010C PollCommandManager       [01]  (1 frame(s))
  0x810C  PollCommandManager       len=4    [00 01 01 01]
```

Add `--json` to any subcommand for machine-readable output.

### Step 6 — the gesture map (read, and an optional reversible write)

```console
$ python -m tlcli --address "$BUDS" keys get
key functions: status=0 count=10
  2:1:1:0   right single-click -> off
  2:1:2:1   right double-click -> play-pause
  2:1:3:6   right triple-click -> next-track
  2:1:4:8   right long-press -> noise-control
```

`keys get` is read-only. `keys set DEVICE:BUTTON:ACTION:FUNCTION` patches one
row and writes the **whole** table back; `keys disable-long-press` sets every
long-press row (`action` 4 or 5) to `0` (off). Both are writes: they refuse
without `--confirm`, and `--save FILE` keeps a restore point. Each field takes a
number (`0x` allowed) or a label such as `right`, `long-press` or
`noise-control`.

```console
$ python -m tlcli --address "$BUDS" keys set left:1:4:noise-control --save before.json --confirm
$ python -m tlcli --address "$BUDS" keys disable-long-press --save before.json --confirm
```

A `restore` of a `backup` snapshot re-applies the table too.

### Step 7 — decode what the device pushes

```console
$ python -m tlcli --address "$BUDS" events --seconds 15
listened 15s: 3 packet(s)
  0x0500  requestHostTime  request: {'raw': '670a0800', 'value_le': 526951}
  0x0204  notificationEvent  event=battery  body=03016402640300
  0x0204  notificationEvent  button press: play-pause  (dev=2 btn=1 action=2 scene=0)
```

`events` listens and decodes `0x0204` notifications (button presses, battery,
wear), device → app `0x05xx` requests, and any state push it recognises. Apart
from the read-only capability poll it sends nothing.

### Step 8 — voice over: speak text through the headset (host-side)

The TL protocol has **no text-to-speech command** — the AI/translation family
(`0x0428 setTranslationAppStatus`, `0x0516` device→app audio) is a server/app
feature with an unrecovered, unverified wire layout, so it is not implemented.
`tlcli say` instead synthesizes speech on the host and plays it to the **current
audio output**, so set the headset as the macOS output device.

```console
$ python -m tlcli say "Noise control is on."
$ echo "piped text works too" | python -m tlcli say
$ python -m tlcli --address AA-BB-CC-DD-EE-FF say --status   # speak a live summary
$ python -m tlcli say --voice Samantha --rate 180 --repeat 2 "pairing mode"
$ python -m tlcli say --out cue.aiff "write a file instead of playing"
$ python -m tlcli say --list-voices | grep en_
```

`--status` connects, negotiates capabilities, reads the device, and speaks one
sentence (it also prints it):

> realme Buds Air7 Pro, firmware 1.1.0.85, battery 100 percent left and 100
> percent right, in ear, noise mode normal, use time 16 hours 6 minutes left and
> 16 hours 9 minutes right.

Pieces the device does not report are skipped. `--out` writes an AIFF and plays
nothing, which also lets the feature be tested without making noise. macOS only
(it uses `say`); on other platforms play the text with your own TTS engine to the
headset's audio output.

### Step 9 — the same thing in the app

The desktop app (`tlapp`) opens the same device and exposes the features built so
far: connect, the capability view, device status, and the noise selector. Its
first slice covers Steps 1–3; see [`app.md`](app.md) for exactly what is in it.

```console
$ python -m tlapp --replay fixtures/spp-smoketest.jsonl   # no device, no radio
$ python -m tlapp --address AA-BB-CC-DD-EE-FF --connect
```

Run it against `--replay` first: that exercises the whole app with no hardware,
which is the quickest way to tell a UI problem from a transport problem. Only
**one** instance can run at a time (the headset has one RFCOMM channel); a second
launch reports which process holds it and exits.

## 4. Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `ChannelRefused … 0xE00002BC` | The link is up but the SPP server refused the channel. Common while the audio link is active. | Re-run with `--reset`, which drops and re-establishes the ACL link first. |
| `PageTimeout … 0xE00002D6` | The device did not answer — asleep, out of range, or off. | Wake the buds (open the case), bring them closer, retry. |
| `not worn … 0x8404 0e` | The write was refused because the buds are not in an ear. | Put the earbuds in and retry. This is the expected in-ear gate. |
| `timeout … no 0x81xx reply` | No reply within the timeout. | Retry with `--reset`; raise `--timeout` for a sleepy device. |
| `CASE=0%` while the buds are out of the case | Reported case level 0 means "not docked", not "empty". | Expected; put the buds in the case to read the real level. |
| SPP cannot be opened at all | Channel 12 is intermittently refused while audio is playing. | Stop audio playback, or `--reset`, or re-pair. |
| `tlapp: OpenRealLink is already running` | The single-instance lock is held, normally by a window you already have open. | Use that window, or quit it first. A lock left by a killed process is reclaimed automatically. |

## 5. Recorded result

Run on **2026-10-18** against a realme Buds Air7 Pro (address redacted),
read-only:

```console
$ python -m tlcli --address <device> --reset smoke
live smoke test: <device>
  [PASS] capability bitmap (0x8100)       49 commands, status=0
  [PASS] status poll (0x0101..0x0128)     model=0x064C12 fw=1.1.0.85 mode=0x01(Normal (noise off)) battery[L=100% R=100% CASE=0%] wear=in-ear
RESULT: OK
```

The first attempt without `--reset` was refused with `0xE00002BC`; the reset
cleared it, which is the documented recovery in §4.

The **noise-mode write** was then verified live, all six values read back over
SPP with the buds worn:

```console
$ python -m tlcli --address <device> --reset mode cycle
noise mode cycle (starting 0x01 Normal (noise off))
  [PASS] 0x01 Normal (noise off)   -> 0x01
  [PASS] 0x02 Transparency         -> 0x02
  [PASS] 0x04 ANC light            -> 0x04
  [PASS] 0x08 ANC deep             -> 0x08
  [PASS] 0x10 ANC medium           -> 0x10
  [PASS] 0x20 ANC smart            -> 0x20
restored 0x01 (Normal (noise off))
RESULT: OK
```

### Expert surface re-check (read-only, 2026-10-23)

The nine Expert-surface reads and the `0x0112` paired-device list were re-driven
through the **app's own controller** against the same Air7 Pro. Every value
matched what is recorded below: vendor `0x072F`, protocol `000001`, colour
`0x06`, one upgrade-capability entry, `0x010D` still empty, push types
`{2, 1}`, no personalized-ANC result, AI idle, and the bass engine at
`type 1/2/3` with `min -5 / max +5 / current 0`. The paired list returned seven
entries, one active, at the same record stride. Nothing here changed the
protocol; the run exists to confirm the app's controller path, not the layouts.

### Feature-switch probe (read-only)

`0x010D` is the read half of the switch/feature pair (`0x0403` writes it). On
the Air7 Pro it comes back empty:

```console
$ python -m tlcli --address <device> --reset send 0x010D --read 1.5
tx 0x010D PollCommandManager       []  (1 frame(s))
  0x810D  PollCommandManager       len=2    [00 00]
```

Status 0 with **zero entries**: the unit advertises the capability bit but
exposes no switch features, and `0x0129 getStatusFeature` went unanswered. Its
boolean toggles therefore go through the dedicated set commands, not `0x0403`.
The same read also pulls the multi-point paired-device list over a `0x0204`
push (not shown).

The write was driven on 2026-10-21 to settle it, and the answer is why `0x0403`
is **not** implemented:

```console
$ python -m tlcli --address <device> send 0x0403 0101 --read 3
   tx 0x0403 SetCommandManager        [01 01]  (1 frame(s))
     0x8403  SetCommandManager        len=1    [00]      # accepted!
$ python -m tlcli --address <device> send 0x0403 0000 --read 2
   tx 0x0403 SetCommandManager        [00 00]  (1 frame(s))
     0x8403  SetCommandManager        len=1    [00]      # accepted too
```

The device accepts **any** `[feature][enabled]` -- `01` is a real
`SWITCH_FEATURE` code and `00` is not one at all -- and changes nothing either
time (`0x010D` still `00 00`, no `0x0503` push). It does not even validate the
feature id, so there is no observable effect to verify a write against.
**Documented, not implemented** (see `command-coverage.md`).

### EQ / sound-effect probe (write, reversible)

The sound-effect preset is a plain mode value: `0x010F` reads it, `0x0406`
writes it, and the write is **read back identity**, so it is trivially
reversible. Take a backup first, then:

```console
$ python -m tlcli --address <device> --reset eq
eq mode   : 4
  * eqId=4  min=1 max=2  bands=0
```

Sweeping `0x0406 0`..`6` and restoring the original mode on the Air7 Pro gave:

| `0x0406` written | `0x810F` read back | `0x0122` `isSelected` |
| --- | --- | --- |
| `0`..`3` | `0`..`3` | `0` |
| `4` (baseline) | `4` | `1` |
| `5`, `6` | `5`, `6` | `0` |
| `7`, `8` | `6` (clamped) | `0` |

So `0x010F`/`0x0406` need no mapping table, and `restore` re-applies the preset
from `0x010F` directly. `eq set-mode <n>` writes it by hand; always re-read to
confirm.

### High-volume probe (write, reversible)

`0x0110` reads a one-byte loudness boost and `0x0407` writes it; the reply is
`0x8110 [status][0|1]`. The write is **read back identity** (a boolean), so it is
trivially reversible:

```console
$ python -m tlcli --address <device> --reset high-volume
high volume: on
$ python -m tlcli --address <device> --reset high-volume off
high volume: wrote off, read back off
$ python -m tlcli --address <device> --reset high-volume on
high volume: wrote on, read back on
```

The device also pushes `0x0204 0f 08 00 <value>` when the setting changes. Like
every settings write it is accepted only while the buds are worn.

### Find-mode probe (write, audible)

`0x0400` rings the buds so you can locate them. It has **no read pair** -- the
only reply is the `0x8400 <status>` ack -- and the vendor app never sends it, so
it is the one command here recovered from decompiled code and then driven
straight at the hardware. Both values were confirmed on the Air7 Pro
(2026-10-20): `01` and `00` each answer `0x8400 00`, and the buds **audibly
rang** on `01` and stopped on `00`.

Because it is **loud**, the command always stops it again, from a `finally`:

```console
$ python -m tlcli --address <device> --reset find
find mode: ON -- the buds are ringing; stopping in 10s (Ctrl-C also stops)
find mode: off
```

`--seconds N` sets the ring time (`0` rings and stops at once). If a run was
killed mid-ring, `--stop` is the recovery path -- it sends `0x0400 00` alone.
Take the buds out of your ears first.

**The app sends the stop from both of its exits.** Re-checked on the Air7 Pro
(2026-10-23): with the buds out and ringing, closing the window and disconnecting
each sent `0x0400 00` (ack `00`) *before* the link dropped. The same run
confirmed the write is **not** wear-gated -- `in_ear` was false before and after
-- which is the point of a find-my-buds command. The ring was audibly confirmed
by the operator, so the command stands verified end to end again rather than
ack-only.

### In-ear (compactness) probe (write, event-observed)

`0x0405` starts/stops the in-ear sensor; it has no read-back pair, so the result
is read from the `0x0204` **event `0x04`** push. `tlcli compactness` drives the
trigger and listens:

```console
$ python -m tlcli --address <device> --reset compactness start --seconds 6
compactness start: listened 6s, 1 packet(s)
  0x0204  notificationEvent  event=compactness  body=01040204
$ python -m tlcli --address <device> --reset compactness stop
compactness stop: listened 4s, 0 packet(s)
```

The body is two `[earSide][value]` records; `value` is a live status code, not a
constant (see `docs/protocol.md` §7). The trigger is a sensor read and drives no
audio.

### AI / translation status (write, read-back verified)

`0x0428 [state][type]` activates (`state=1`) or deactivates (`state=0`) the
AI/translation session; `0x0131` reads it back as `[status][state][type]`.
`state=0` applies only with the matching active `type`, and the state is
session-scoped (it resets when the link closes). `tlcli ai-status` reads and
writes it:

```console
$ python -m tlcli --address <device> --reset ai-status get
ai status : state=0 type=0
$ python -m tlcli --address <device> --reset ai-status set 1 1
ai status : wrote state=1 type=1, read back state=1 type=1
$ python -m tlcli --address <device> --reset ai-status set 0 1
ai status : wrote state=0 type=1, read back state=0 type=0
```

`type=4` (AI chat) is accepted at the protocol level but not applied by FW
`1.1.0.85` (the read stays `00 00`). The device also pushes
`0x0204 0f 02 00 <state> <type>` on every change.

### Personalized-noise result and notification capabilities (read-only)

Two more verified reads round out the poll/notify surface:

* `0x011A` returns `[status][exists]` — whether the device has a stored
  personalized-noise calibration result. This unit reports `00 00` (no stored
  result). It is **not** a read-back of a write: the companion `0x0412` is a
  calibration *trigger* whose result arrives on the `0x0204` event `0x0B`.
* `0x0200` returns `[status][count][type...]` — the `0x0204` event types the
  device can push. This unit reports `00 02 02 01` (types `0x02` and `0x01`).

```console
$ python -m tlcli --address <device> --reset personalized-noise
personalized noise : result stored=False (exists=0)
$ python -m tlcli --address <device> --reset notification-caps
notification capabilities: 2 type(s): 0x02, 0x01
```

### Identity / status fields (read-only)

Four always-on reads expose the device identity. `tlcli identity` reads them in
one pass:

* `0x0102` -> `[status][vendorId:2 LE]` (`0x072F`)
* `0x0104` -> `[status] + version bytes` (the host announces its protocol
  version and the device answers `00 00 00 01`)
* `0x0107` -> `[status][count] + count x [deviceType][flags]`
  (`support_ota = flags & 1`, `ota_type = (flags >> 1) & 1`)
* `0x010B` -> `[status][colorId]` (`6`)

```console
$ python -m tlcli --address <device> --reset identity
vendor id : 0x072F
protocol  : 000001
color id  : 6
upgrade   : type=1 ota=True/1
```

### Device → app request pushes (read-back side of a write)

The device confirms some settings by pushing a `0x05xx` request the app parses,
rather than answering with a read reply. Three of them are live-verified
(Air7 Pro, 2026-10-20):

| write | push | body |
| --- | --- | --- |
| `0x0406 [mode]` | `0x0504` | `[eqMode]` |
| `0x0418 [05]` (query) | `0x0506` | custom-EQ list |
| `0x041B [min][max][current]` | `0x0508` | `[min:int8][max:int8][current:int8][type:int8]` |

The `0x0508` range is **signed** (`0x041B ...` against `-5..+5` yields
`fb 05 07 00` = `min -5, max +5, current 7`). The device keeps its own fixed
range and only echoes `current` from the write. `tlcli events` decodes these
via `decode_request`.

### Human-triggered pushes (gesture + noise events)

Some pushes only appear when a person acts on the headset. A guided gesture run
(Air7 Pro, FW `1.1.0.85`, 2026-10-20) verified two of them:

| push | body | notes |
| --- | --- | --- |
| `0x0204` event `0xF1` | `[deviceType][button][buttonAction][function][scene] + N x [option:2 LE]` | `parse_user_interaction`; left double-click `f1 01 01 02 00 03 00 00`, left long press `f1 01 01 04 00 03`, right long press `f1 02 01 04 00 03` |
| `0x0204` event `0x03` | `[a][b][c]`, 3 bytes (`NoiseReductionInfo`) | the noise-mode mirror `03 01 01 <mode>` plus a companion push `03 04 01 04`; **not** a TLV |

Two things to watch:

* the `0xF1` `function` byte is **not** the `0x0108` table value -- long press
  reported `0` while the table said `8/ANC`, and the ANC cycle still happened;
* `0x0502 requestOperation` was **not** pushed for any gesture -- play/pause,
  track skip and ANC are firmware-side, so the app is never asked.

A follow-up bound every left gesture to the app-side function `31` and tapped;
it captured only `action=0` and `action=3`, never the expected `action=2`, so
the `0x0502` question is still open. `action = 0` has **no entry in the gesture
map** (`1` single, `2` double, `3` triple, `4`/`5` long, `6` extra) and its
meaning is unknown -- treat it as an opaque value, do not map it.

A later session found why the `action=2` press was never seen, and it is worth
knowing before re-running this: **a gesture bound to function `31` reports no
press at all.** With the LEFT double-tap written to the table as `31` there were
zero `0xF1` events across three rounds (one of them with the AI status active);
with the same gesture bound to `1` there were 19, 21 and (unarmed) 11, in one
sitting, each armed round proving its restore with a `0x0108` read-back. Arming
only that one row reproduces it, so the suppressor is the function **value**,
not the act of writing the table. The press is **dropped, not queued**: one late
`0xF1` did arrive after the `31` binding was removed, but the wearer confirms it
was a deliberate tap *after* the window, and the count agrees — roughly twenty
in-window presses produced none, while one post-window press produced exactly
one. Either way the earlier route to `0x0502` is closed, and the ledger in
[`command-coverage.md`](command-coverage.md) says so.

Replay fixture: `fixtures/user-interaction.jsonl` (sanitized).

### Backup and restore

`backup` is read-only and can be run at any time:

```console
$ python -m tlcli --address <device> --reset backup settings.json
backup written to settings.json
  device    : {'model': '0x064C12', 'firmware': ['1.1.0.85']}
  captured  : 17 responses
  restorable: eq_mode, high_volume, key_function, noise_mode
```

`restore --dry-run` prints the plan without sending a write; `restore` then
re-applies the symmetric settings and reports everything else as `not_restored`:

```console
$ python -m tlcli --address <device> --reset restore --dry-run settings.json
  [DRY-RUN] noise_mode
  [DRY-RUN] key_function
  [DRY-RUN] eq_mode
  [DRY-RUN] high_volume
  [SKIP]    eq_info (captured, no restorer)
```

There are now **four** restorable settings, and all four -- **noise mode, EQ
mode, key function and high volume** -- have been verified live end to end
(above, and the long-press disable in `docs/protocol.md` §8). Two settings used
to be restorable and were dropped after live testing on 2026-10-21: the
switch-feature write (`0x0403`, accepted but a provable no-op) and the
bass-engine write (`0x041B`, which *is* verified but has no working **read** to
capture a value with, since `0x0124` is never answered). As always, a settings
write requires the buds to be **worn** (§2).

## 6. What to record

Copy the `smoke` output into the issue or commit message. A successful run
confirms, on this codebase:

1. SDP discovery finds the SPP channel.
2. `MacOSTransport.open`/`read`/`write` move real frames (the run-loop pump).
3. The capability bitmap parses and the poll decoders are correct.
4. `mode set` round-trips through the device when worn.

If any step fails, the `[FAIL]` row names the exception; that traceback plus the
device state is the useful artifact.
