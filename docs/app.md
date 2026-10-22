# OpenRealLink — the desktop app

Status: **fifth slice.** The app is built one feature at a time, and a feature
reaches the UI only once it is implemented *and* verified. These slices expose
eight things and nothing else:

1. **Connect + capability view** — open the device and read which commands it
   advertises (`0x8100`).
2. **Device status** — model, firmware, version, battery, wear, active noise
   mode, and use time.
3. **Noise mode get / set** — read `0x010C`, write `0x0404`, read back.
4. **Equalizer preset get / set** — read `0x010F`, write `0x0406`, read back,
   plus the `0x0122` preset / custom-EQ list.
5. **High volume get / set** — read `0x0110`, write `0x0407`, read back.
6. **Settings backup / restore** — poll every setting the unit advertises into a
   JSON snapshot, and re-apply the restorable ones from one.
7. **Gesture table get / set** — read `0x0108`, edit the function bound to each
   gesture, write the whole table back with `0x0401` and read it again.
8. **Push-event monitor** — listen for the device's own notifications (button
   presses, battery, wear, noise, multi-connect) and decode them live.

Everything else the CLI can do (voice, raw console, the SDP service list) is
deliberately **not** in the UI yet. It is added, wired up and live-tested one
item at a time, so the app never carries a control for something that is not
implemented.

## Running it

Set up and activate the repository virtualenv first (see the
[README](../README.md)); every `python` below means that interpreter:

```console
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e ".[dev,macos,gui]"
```

Then:

```console
$ python -m tlapp                                   # connect by hand
$ python -m tlapp --address AA-BB-CC-DD-EE-FF --connect
$ python -m tlapp --replay fixtures/spp-smoketest.jsonl
```

`--replay` runs the whole app against a recorded capture — no device and no
radio — which is how it is developed and demonstrated. The window connects as
soon as it opens. The replay fixtures carry no device address, so they are safe
to use anywhere.

### One instance at a time

The headset has a **single** RFCOMM slot, so a second copy of the app would fight
the first for the channel and fail with a bare `0xE00002BC` refusal. The app
instead takes a lock file (under the platform cache directory, via
`QStandardPaths`) and a second launch reports which process already holds it and
exits `1`:

```console
$ python -m tlapp
$ python -m tlapp
tlapp: OpenRealLink is already running (pid 12345 on host). Use the window that
is already open; only one instance can hold the headset's RFCOMM channel.
```

The refusal goes to **stderr**, never a modal dialog, so a scripted or headless
launch cannot hang waiting for a click. A lock left behind by a killed process
is reclaimed automatically, because `QLockFile` records the holder's PID. The
path can be overridden with `OPEN_REAL_LINK_LOCK` to run two builds side by side.
`SIGINT` and `SIGTERM` close the window cleanly and release the lock.

## What the window does

### Device

* **Connect / Disconnect** and a live connected/disconnected state.
* **Read status** fills in the model (resolved to a name from the static
  candidate list), firmware, version, battery for left/right/case, wear state,
  the active noise mode, and use time.
* **Read capabilities** shows the `0x8100` capability bitmap: every known
  command, its manager class, and whether this unit advertises it (`yes`,
  `always-on`, or blank).

On connect the app reads capabilities and status once, so the window is already
populated.

### Sound

* **Active noise mode** — the six modes (`Normal`, `Transparency`, `ANC light`,
  `ANC deep`, `ANC medium`, `ANC smart`). **Read** shows the current mode;
  **Apply** writes the selected mode and reads it back.
* **Equalizer** — the preset selector offers the verified `0..6` range only.
  **Read** shows the active preset, **Apply** writes it and reads it back, and
  **Read presets** fills a table from the device's own `0x0122` list
  (*selected*, *eqId*, *min*, *max*, *bands*, *name*) rather than a shipped
  table, because no preset names were recovered.
* **High volume** — the loudness-boost flag. **Read** sets the checkbox to the
  device's answer; **Apply** writes it and reads it back.

A noise-mode or high-volume write is accepted only while the buds are **worn**;
otherwise the device answers `0x8404 0e` / `0x8407 0e` and the app says so
instead of looking broken. Apply reads each setting back because the
asynchronous push can arrive out of order — the read is authoritative. The same
is true for the equalizer: writing outside `0..6` makes the device clamp the
value, so **Apply** compares the read-back and says so instead of pretending the
write took.

### Gestures

Each row binds one gesture on one earbud to one function; the unit's own table is
the starting point, never a shipped guess.

* **Read table** fills the rows: device, button, gesture, the function as a
  dropdown, and what it was.
* **Disable long press** stages `off` on every long-press row. It **stages**,
  it does not write — the user reviews the whole table and clicks Apply, so there
  is one write path and one confirmation.
* **Revert** puts every dropdown back to what the device last reported.
* **Apply changes** confirms, then writes. `0x0401` replaces the **whole**
  table, so the app re-reads it, patches only the rows you changed, writes every
  row back, and reads it again. A read-back that does not match what was written
  is reported as a mismatch rather than assumed to have worked.
* Rows with nothing changed are never sent, and if the whole table is unchanged
  the app does not write at all.

Two safety properties are deliberate:

* A gesture the device did not report is refused, and the refusal happens
  **before** anything is sent — a partial edit must never silently drop the rows
  it did not name.
* A function byte this build has no name for stays selectable and unchanged
  (shown as `0x42 (unnamed)`). Rewriting it to `off` because the dropdown could
  not represent it would disable the user's gesture the moment they touched
  anything else.

Function names are the app's own enum; the unit's table proves the encoding on
**read**. Whether the unit will *take* a given value is decided by the read-back,
not by the dropdown.

### Backup

* **Back up now** polls every setting the unit advertises (the same curated plan
  the vendor app uses, since the protocol has no "read all settings" command)
  and writes one plain JSON file: device identity, the capability bitmap, the
  **raw reply** of each poll, and the derived write-back values. Requests the
  unit refuses or does not answer are recorded as *skipped*, not fatal.
* The summary under the buttons says which settings are **restorable** and which
  were *captured but have no recovered writer* — reported, never guessed at.
* **Preview** reads a file and reports exactly what a restore would do, using the
  core's own dry run, so a preview and a real restore cannot disagree. Nothing is
  written to the device.
* **Restore** asks for confirmation, re-applies the restorable settings, and
  lists every setting with its outcome. Settings the unit does not support are
  reported as skipped rather than attempted.

A snapshot is **personal data**: if the unit supports `0x0112`, the file embeds
this host's paired-device names. The tab says so; redact before sharing.

#### Why plain files, not a database

A snapshot is a **document, not a dataset** — one identity, one bitmap, a dozen
raw replies and a flat settings dict, a few kilobytes in total. Nothing queries
or joins it, nothing updates it in place, and the whole point is that a person
can open it, read the raw hex, redact it and attach it to a bug report. So
`tlapp/backupfile.py` writes `json.dumps(snapshot, indent=2)` and nothing else,
which also means a file written here restores from `tlcli restore` and a file
written by `tlcli backup` restores here. The format tag
(`open-real-link.backup/1`) *is* the schema version, and a file that does not
carry it is refused rather than replayed onto a device.

The things a database would have been standing in for are handled directly:
writes are atomic (temp file + `os.replace`, so a crash cannot leave a
half-written backup), the file is validated on read, and a backup library is a
**directory of timestamped files** (`buds-air7-pro-20261020T120000Z.json` under
the platform data directory) that `ls`, `diff` and `git` already understand.

### Events

* **Start / Stop listening** toggles the monitor. Nothing is *sent* while it is
  on — the app only reads — and it is off by default, because listening holds the
  link in short slices.
* Each push becomes a row, newest first: the time, the event name (or the
  command name for a `0x05xx` request), and a one-line description. A button
  press shows its device, button, action and scene; a battery push shows its
  decoded TLV; a multi-connect push shows the count; a packet no decoder
  understood still shows its raw payload rather than being dropped.
* **Read event types** asks the unit which `0x0204` types it can push (`0x0200`,
  ungated) and reports them in the status bar.
* **Clear** empties the table.

Pushes that arrive while some other operation is waiting for its reply are not
lost: the core parks them, and the monitor drains that queue too, so nothing is
shown twice and nothing disappears.

#### Why a timer, not a reader thread

The monitor polls from a `QTimer` — a 50 ms read budget every 60 ms — rather than
running a background reader. A thread would keep the UI perfectly smooth, but the
link has **one** reader and the controller is deliberately single-threaded: a
background reader would need a lock held across every other operation (so a
user's click would still wait behind the read) *and* a transport that tolerates
being read from two threads at once, which is not something this project has
established for IOBluetooth RFCOMM. The timer costs at most one poll budget of
input latency and cannot corrupt the session. A read failure stops the monitor
and reports it in the status bar — never a modal, because a modal opened from a
`QTimer` callback spins a nested event loop.

## Design

Widgets live in `tlapp/window.py`; every device action goes through
`tlapp/controller.py`, which is **Qt-free** (`import tlapp` never imports
PySide6). Snapshot file I/O is in `tlapp/backupfile.py`, also Qt-free, so the
format can be tested without a GUI. The single-instance guard is in
`tlapp/instance.py`. That split is what makes the app testable:

* `tests/test_app.py` drives the controller against recorded and synthetic
  fixtures — no display, no radio, no hardware;
* `tests/test_app_gui.py` builds the window on the offscreen Qt platform and
  connects against a fixture, checks the lock is exclusive and reclaimable, and
  launches the real `main()` in a subprocess to prove it reaches the event loop.
  It skips cleanly when PySide6 is absent.

The controller opens the device exactly the way the CLI does, by reusing
`tlcli.cli.open_session`, so `--replay` and the live path behave the same.

## Safety

* The hearing-test / ear-scan tones, OTA and debug families have **no control**
  in the UI — they are documented but not implemented
  ([`protocol.md`](protocol.md) §7).
* Controls are gated by the unit's own capability bitmap: **Apply** is disabled
  when the device does not advertise the write.
* A whole-table write (`0x0401`) is only ever built from a table the device just
  reported, is confirmed first, and is read back afterwards.
* A noise write that the device rejects is reported in plain words, not as a
  raw status code.
* Only one instance runs at a time, so two copies cannot fight over the headset's
  single RFCOMM channel.

## What is next

The remaining verified features, each added the same way: host-side voice, a
guarded raw console, and the SDP service list.
