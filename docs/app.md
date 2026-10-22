# OpenRealLink — the desktop app

Status: **second slice.** The app is built one feature at a time, and a feature
reaches the UI only once it is implemented *and* verified. These slices expose
five things and nothing else:

1. **Connect + capability view** — open the device and read which commands it
   advertises (`0x8100`).
2. **Device status** — model, firmware, version, battery, wear, active noise
   mode, and use time.
3. **Noise mode get / set** — read `0x010C`, write `0x0404`, read back.
4. **Equalizer preset get / set** — read `0x010F`, write `0x0406`, read back,
   plus the `0x0122` preset / custom-EQ list.
5. **High volume get / set** — read `0x0110`, write `0x0407`, read back.

Everything else the CLI can do (EQ, gestures, events, backup/restore, voice, raw
console) is deliberately **not** in the UI yet. It is added, wired up and
live-tested one item at a time, so the app never carries a control for something
that is not implemented.

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

## Design

Widgets live in `tlapp/window.py`; every device action goes through
`tlapp/controller.py`, which is **Qt-free** (`import tlapp` never imports
PySide6). The single-instance guard is in `tlapp/instance.py`. That split is what
makes the app testable:

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
* A noise write that the device rejects is reported in plain words, not as a
  raw status code.
* Only one instance runs at a time, so two copies cannot fight over the headset's
  single RFCOMM channel.

## What is next

The remaining verified features, each added the same way: the gesture table and
a long-press disable, an event/button-press monitor, settings backup and
restore, host-side voice, a guarded raw console, and the SDP service list.
