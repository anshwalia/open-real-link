# OpenRealLink — tech stack

Status: **decided.** Python 3.9+, PySide6 (Qt 6) for the UI, built CLI-first.

This document records the constraint that forced the choice and what was ruled
out, so the decision does not have to be re-argued. The shape of the code is a
separate matter — see [`architecture.md`](architecture.md).

---

## 1. The constraint that picked the stack

Everything else is negotiable. **The control channel is Bluetooth Classic
RFCOMM/SPP** — UUID `00001101-0000-1000-8000-00805F9B34FB`, SDP channel 12 on
the Buds Air7 Pro, OPPOv1 framing, ~10 ms command spacing.

This is established, not assumed:

* the Air7 Pro's RUS config carries **no BLE/GATT key** — only
  `"sppUUIDType": "TYPE_DEFAULT"`;
* the live SDP record exposes Classic channels 12 / 15 / 17;
* a direct GATT probe found **no usable control path**. The services the device
  advertises are Fast Pair and vendor-specific, and **none of them appear in the
  app's own GATT tables** — the app knows `0000079A`, the device offers
  `0000079C`. A model-id read succeeded; the control writes do not exist there.

That last point was tested rather than reasoned about, and the result is
decisive. There is no second route to the device. The GATT UUIDs in
`UUIDStorage` (`…-irohaBLE`) belong to **other** TL models, not this one.

### Platform viability for Classic RFCOMM

| platform | API | viable? |
|---|---|---|
| **macOS** | `IOBluetooth.framework` (`IOBluetoothRFCOMMChannel`) | ✅ proven on this device |
| **Windows** | WinSock `AF_BLUETOOTH` / `BTHPROTO_RFCOMM` | ✅ |
| **Linux** | BlueZ `AF_BLUETOOTH` / `BTPROTO_RFCOMM` | ✅ — plain `socket` module |
| **Android** | `BluetoothSocket` | ✅ available, not in the first cut |
| **iOS** | `ExternalAccessory` — MFi accessories only | ❌ not MFi; `CoreBluetooth` is BLE-only |
| **Web** | Web Bluetooth — BLE GATT only | ❌ |

**So "cross-platform" means macOS + Windows + Linux + Android.** iOS and the
browser are not reachable for this device, and no stack choice changes that.

---

## 2. What the app actually has to do

| layer | work | status |
|---|---|---|
| **transport** | SDP/connect, read/write bytes, reconnect, discover channel | `tltransport/` — macOS (`macos.py`) and Windows (`windows.py`) live transports, plus a hardware-free `replay`. Linux has **no live transport**: `--transport auto` there raises `TransportUnavailable` naming the platform, rather than falling back to a recording |
| **framing** | OPPOv1: `0xAA`, LEB128 length, control byte, FSN, multi-frame reassembly | ✅ `tlcore.wire` |
| **packet** | 5-byte LE header, response bit, manager dispatch | ✅ `tlcore.wire` |
| **command table** | 79 IDs, 61 capability groups, always-on set | ✅ `tlcore.commands` |
| **capability gate** | decode `0x8100` bitmap → which commands this unit accepts | ✅ `tlcore.commands` |
| **device model** | noise vocabulary, decoders for battery / wear / mode / EQ / use-time | ✅ `tlcore.noise`, `tlcore.decoders` |
| **settings** | backup / restore (noise mode, key function, EQ mode, high volume) | ✅ `tlcore.backup`, `tlcli backup` / `restore` |
| **UI** | connect, capability view, noise selector, firmware, paired list, EQ, battery/wear, raw console | ⚠️ **in progress** — `tlapp/`: connect, capability view, status, noise mode, EQ preset + `0x0122` list, high volume, gesture table, backup / restore, push-event monitor, find my buds, paired-device list, a connect-time compatibility check, a Bluetooth-off pre-flight gate, a consistent visual theme, and two views — General and Expert (diagnostics + bitmap + bass + guarded raw console) ([`app.md`](app.md)) |

`tlcore` is the entire reverse-engineering result, and it is done: ported by
range extraction from the validated reference, verified byte-for-byte against it.
So the work that is left is a UI plus the Linux transport shim — the macOS and
Windows transports are both live-verified. The UI is now started and is built
one verified feature at a time.

Two assets make both the remaining work and any future port cheap:

* `fixtures/*.jsonl` — recorded device traffic, byte-exact golden input;
* `tests/test_parity_tl_frame.py` — the reference behaviour, as an executable
  specification any port can be checked against.

### Constraints the app must honour

1. **Never expose the hearing / ear-scan commands** (`0x011E`, `0x011F`, `0x040D`,
   `0x040E`, `0x0415`, capability bits 19/30) in the UI without a hard warning —
   they drive **loud test tones**. Only `0x0404 action=1` (noise mode) is safe.
2. **Never expose OTA (`0x0300`–`0x0308`) or debug (`0x0F00`–`0x0F02`).** OTA is
   a firmware push with no read-back; debug toggles firmware verbosity. Neither
   has a builder, decoder or UI control — they are documented in
   [`protocol.md`](protocol.md) §7 only. The one thing that touches their IDs is
   the `tlcli send` **refusal**, which prevents traffic rather than enabling it.
3. **Noise writes are gated on in-ear detection** — the UI must explain
   `0x8404 0e` ("put the buds in") rather than look broken.
4. **No account, no telemetry, no vendor server.**
5. **GPL-3.0-or-later.**

---

## 3. The decision

**Python 3.9+ for everything. PySide6 (Qt 6) for the UI. Built CLI-first.**

| | |
|---|---|
| protocol + transport + UI | Python — one language, no native code |
| RFCOMM | macOS pyobjc/IOBluetooth (working) · Linux stdlib `socket` · Windows `ctypes` → WinSock |
| UI | PySide6 — Qt Quick/QML or QWidgets, one codebase for macOS/Windows/Linux |
| reuse | **100 %** — `tlcore` imported directly, not ported |
| packaging | PyInstaller (macOS and Windows builds; see [`packaging.md`](packaging.md)) |
| Android | not in the first cut |

**Why this one.** It reuses the verified protocol verbatim. It keeps one language
across the reverse-engineering work and the app, so there is no second
implementation of the wire format to keep in sync. And the only code that does
not already exist is a UI plus the Linux transport shim (the macOS and Windows
transports are live-verified) — which is why the first real
release is about a week of work rather than a month.

**What it costs.** No Android in the first cut, and Qt/PyInstaller packaging is
the genuinely annoying part. Neither affects the core, which is where the value
is and which is why Android stays reachable later (§5). Both the macOS and
Windows transports have been live-verified on hardware (`live-test.md`).

### 3.1 The Python floor — 3.9, kept (re-checked 2026-11-15)

`requires-python = ">=3.9"`, and CI's matrix tests the floor itself (3.9) plus
3.13. The floor was re-examined against real usage data and **kept**; this is
the evidence, so it does not have to be gathered again.

**What people actually run.** PyPI download logs by interpreter, summed over 16
high-traffic packages (`requests`, `urllib3`, `certifi`, `setuptools`, `pip`,
`numpy`, `pandas`, `boto3`, `typing-extensions`, `six`, `pytest`,
`cryptography`, `pillow`, `pyyaml`, `django`, `flask`) for
2026-10-16 … 2026-11-14 — 18.0 billion downloads, read from
`pypistats.org/api/packages/<pkg>/python_minor`:

| minor | share | |
|---|---|---|
| **3.12** | **30.9 %** | the most-used version, and not this project's floor |
| 3.11 | 16.4 % | |
| 3.13 | 15.2 % | the other CI leg |
| 3.9 | 13.7 % | the floor — mostly one runtime, see below |
| 3.14 | 8.7 % | previous stable, released 2025-10 |
| 3.10 | 8.5 % | EOL since 2026-10-01 |
| 3.7 / 3.8 / 3.6 / 2.7 | 1.5 / 1.3 / 0.2 / 0.2 % | |
| 3.15 | 0.9 % | newest release, 2026-10-09 |

Roll-up: **3.10+ 80.6 %**, ≤3.9 17.0 %, unclassified 2.4 %.

**The 13.7 % on 3.9 is mostly one runtime.** Of `boto3`'s own downloads, 62.5 %
arrive on 3.9 — AWS Lambda's managed `python3.9` runtime, which is still
available. Excluding `boto3`: 3.9 is **5.7 %**, 3.12 is **34.6 %**, 3.10+ is
**88.3 %**. Packages that application code pulls in directly sit much further
forward than the aggregate: `pytest` 94.8 % on 3.10+, `pillow` 92.6 %, `pyyaml`
93.1 %, `flask` 91.8 %, `django` 90.4 %. Conda-forge, counted all-time (so it
lags), gives 3.10 17.5 %, 3.9 15.4 %, 3.12 14.9 %, 3.11 12.5 %, 3.13 7.0 %,
3.14 4.4 %.

**Support status** (python.org developer guide / endoflife.date): 3.9 reached
end-of-life on **2025-10-31**, 3.10 on **2026-10-01**. In support: 3.11
(security, to 2027-10), 3.12, 3.13, and 3.14 / 3.15 (both bugfix, 3.15 being the
newest release).

**Why keep 3.9 anyway.** Because it costs nothing. The convention that makes it
cheap is already mandatory here — `from __future__ import annotations` plus
PEP 604 annotations ([`../AGENTS.md`](../AGENTS.md) "Conventions") — and no
runtime API newer than 3.9 is used anywhere in `tlcore`, `tltransport`, `tlcli`
or `tlapp`. What the floor buys is a real install base; what it must **not** buy
is a claim that 3.9 is what people use, because 3.12 is. Revisit when a
dependency forces the issue: on 3.9, pip already resolves PySide6 **6.10.3**,
pytest **8.4.2** and coverage **7.10.7**, where a 3.13 install gets 6.12 / 9.2 /
7.17.

**The extras use floors, not pins — on purpose.** `pytest>=7.0`, `coverage>=7.0`,
`ruff>=0.6`, `PySide6>=6.6`, `pyinstaller>=6.0`: each interpreter resolves the
newest release that still supports it, so the 3.9 and 3.13 CI legs run different
tool versions by design. A test that differs between legs should therefore be
read as version skew first and a bug second.

---

## 4. Alternatives considered and why they lost

Kept as a record.

| option | Android | macOS | reuse | effort | why it lost |
|---|---|---|---|---|---|
| **A** Python + PySide6 | ❌ | ✅ proven | **100 %** | 1 wk | — **chosen** |
| **B** Flutter + native SPP plugin | ✅ | ✅ | port | 3–4 wk | four native shims where one would do, for an Android we are not shipping yet; the Dart port would also have to be kept in sync with `tlcore` |
| **C** Rust + Tauri v2 | ✅ | ⚠️ hand-rolled | port | 4–6 wk | the best long-term *library* artifact — one core crate, usable via FFI from Python, Swift, Kotlin, JS. But `IOBluetooth` has no maintained `objc2` binding, so macOS RFCOMM means hand-rolled `objc2` msgSends or a C shim, and nothing here needs FFI yet |
| **D** Qt 6 / C++ (`QBluetoothSocket`) | ✅ | ✅ | port | 2–3 wk | the closest runner-up: the only stack where RFCOMM is a first-class cross-platform abstraction, so the transport is written once. Lost on toolchain weight (~1 GB, and nothing installed here) and on a C++ port that buys nothing on the Python side |
| **E** .NET 9 + Avalonia + 32feet | ✅ | ❌ | port | 3 wk | 32feet dropped macOS years ago; for a project whose proven platform *is* macOS, a missing macOS transport is backwards |
| **F** KMP + Compose | ✅ | ⚠️ JNA | port | 4 wk | desktop RFCOMM is the worst of any option here — BlueCove is dead, so JNA into IOBluetooth / WinSock / BlueZ by hand |

Options B–F every one of them require porting `tlcore`. That port is ~3,000
lines, mechanical, bounded, and verifiable against fixtures that already exist —
so none of them is a bet on rewriting the project, and they lost on cost and fit
rather than on feasibility.

**Also considered and rejected:** Electron/Node (`bluetooth-serial-port` is
unmaintained and does not build on Node 26); Go + Fyne (no usable RFCOMM);
Swift/SwiftUI (not cross-platform); a local daemon with a browser UI (fine as a
control panel, but not a real app on Android).

---

## 5. Consequences

**Android is deferred, not closed.** If it becomes a goal, the route is a Dart
port of `tlcore` plus a Flutter UI: the transport is written per-platform in
every stack anyway, so it is never reused and never drives the choice. The port
is a 1–2 day cost on one layer, paid once, verified against the fixtures above.
What gets thrown away is the Python UI — and the UI is the cheapest part.

**Everything else follows from the core staying pure.** See
[`architecture.md`](architecture.md) §2: `tlcore` imports nothing
platform-specific, so it can be exercised on a machine with no radio, in CI, or
from another language later. That property is what makes the deferral cheap, and
it is enforced by a test rather than intended.
