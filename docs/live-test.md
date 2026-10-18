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

Check the dependency and that the headset is paired (read-only):

```console
$ python3 -c "import IOBluetooth; print([d.getName() for d in IOBluetooth.IOBluetoothDevice.pairedDevices() or []])"
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
* `send` will happily transmit an arbitrary command id. Prefer the named
  subcommands.

## 3. Run it

Set your device address once for the session:

```console
$ export BUDS=AA-BB-CC-DD-EE-FF
```

### Step 1 — can the host see the device's services? (no bytes sent)

```console
$ python3 -m tlcli --address "$BUDS" services
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
$ python3 -m tlcli --address "$BUDS" smoke
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
$ python3 -m tlcli --address "$BUDS" mode get
noise mode: 0x01  Normal (noise off)
```

### Step 4 — the write, with the buds **worn**

```console
$ python3 -m tlcli --address "$BUDS" mode set anc
set 0x20 (ANC smart); device now 0x20 (ANC smart)
```

Then confirm by ear. `smoke --write` does the same and **restores the original
mode afterwards**:

```console
$ python3 -m tlcli --address "$BUDS" smoke --write anc
  ...
  [PASS] set mode 0x20 (ANC smart)
  [PASS] read mode (after write)          0x20
  [PASS] restore mode 0x01
RESULT: OK
```

`mode cycle` is the definitive write test: it applies **all six** modes, reads
each one back, verifies the round trip, and restores the original mode:

```console
$ python3 -m tlcli --address "$BUDS" --reset mode cycle
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
$ python3 -m tlcli --address "$BUDS" send 0x010C 01 --read 1
tx 0x010C PollCommandManager       [01]  (1 frame(s))
  0x810C  PollCommandManager       len=4    [00 01 01 01]
```

Add `--json` to any subcommand for machine-readable output.

## 4. Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `ChannelRefused … 0xE00002BC` | The link is up but the SPP server refused the channel. Common while the audio link is active. | Re-run with `--reset`, which drops and re-establishes the ACL link first. |
| `PageTimeout … 0xE00002D6` | The device did not answer — asleep, out of range, or off. | Wake the buds (open the case), bring them closer, retry. |
| `not worn … 0x8404 0e` | The write was refused because the buds are not in an ear. | Put the earbuds in and retry. This is the expected in-ear gate. |
| `timeout … no 0x81xx reply` | No reply within the timeout. | Retry with `--reset`; raise `--timeout` for a sleepy device. |
| `CASE=0%` while the buds are out of the case | Reported case level 0 means "not docked", not "empty". | Expected; put the buds in the case to read the real level. |
| SPP cannot be opened at all | Channel 12 is intermittently refused while audio is playing. | Stop audio playback, or `--reset`, or re-pair. |

## 5. Recorded result

Run on **2026-10-18** against a realme Buds Air7 Pro (address redacted),
read-only:

```console
$ python3 -m tlcli --address <device> --reset smoke
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
$ python3 -m tlcli --address <device> --reset mode cycle
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

### Feature-switch probe (read-only)

`0x010D` is the read half of the switch/feature pair (`0x0403` writes it). On
the Air7 Pro it comes back empty:

```console
$ python3 -m tlcli --address <device> --reset send 0x010D --read 1.5
tx 0x010D PollCommandManager       []  (1 frame(s))
  0x810D  PollCommandManager       len=2    [00 00]
```

Status 0 with **zero entries**: the unit advertises the capability bit but
exposes no switch features, and `0x0129 getStatusFeature` went unanswered. Its
boolean toggles therefore go through the dedicated set commands, not `0x0403`.
The same read also pulls the multi-point paired-device list over a `0x0204`
push (not shown).

### EQ / sound-effect probe (write, reversible)

The sound-effect preset is a plain mode value: `0x010F` reads it, `0x0406`
writes it, and the write is **read back identity**, so it is trivially
reversible. Take a backup first, then:

```console
$ python3 -m tlcli --address <device> --reset eq
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

### Backup and restore

`backup` is read-only and can be run at any time:

```console
$ python3 -m tlcli --address <device> --reset backup settings.json
backup written to settings.json
  device    : {'model': '0x064C12', 'firmware': ['1.1.0.85']}
  captured  : 17 responses
  restorable: eq_mode, key_function, noise_mode
```

`restore --dry-run` prints the plan without sending a write; `restore` then
re-applies the symmetric settings and reports everything else as `not_restored`:

```console
$ python3 -m tlcli --address <device> --reset restore --dry-run settings.json
  [DRY-RUN] noise_mode
  [DRY-RUN] key_function
  [DRY-RUN] eq_mode
  [SKIP]    eq_info (captured, no restorer)
```

Of the four restorable settings, the **noise-mode and EQ-mode writes have been
verified live end to end** (above); the key-function and switch-feature writes
are covered offline by `tests/test_transport_and_session.py`. As always, a noise
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
