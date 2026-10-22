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

* the Air7 Pro RUS config (`dn4PRxdiX3hnEQdmbRkdQE5cEEFkWFRTZWlOAg==`) carries
  **no BLE/GATT key** — only `"sppUUIDType": "TYPE_DEFAULT"`;
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
| **transport** | SDP/connect, read/write bytes, reconnect, discover channel | `tltransport/` — macOS ~250 lines, other platforms ~150–200 each |
| **framing** | OPPOv1: `0xAA`, LEB128 length, control byte, FSN, multi-frame reassembly | ✅ `tlcore.wire` |
| **packet** | 5-byte LE header, response bit, manager dispatch | ✅ `tlcore.wire` |
| **command table** | 79 IDs, 61 capability groups, always-on set | ✅ `tlcore.commands` |
| **capability gate** | decode `0x8100` bitmap → which commands this unit accepts | ✅ `tlcore.commands` |
| **device model** | noise vocabulary, decoders for battery / wear / mode / EQ / use-time | ✅ `tlcore.noise`, `tlcore.decoders` |
| **settings** | backup / restore (noise mode, key function, EQ mode, high volume, switch features) | ✅ `tlcore.backup`, `tlcli backup` / `restore` |
| **UI** | scan/pair, connect, capability view, noise selector, firmware, paired list, EQ, battery/wear, raw console | ⚠️ **in progress** — `tlapp/`: connect, capability view, status, noise mode, EQ preset + `0x0122` list, high volume, gesture table, backup / restore, push-event monitor ([`app.md`](app.md)) |

`tlcore` is the entire reverse-engineering result, and it is done: ported by
range extraction from the validated reference, verified byte-for-byte against it.
So the work that is left is a UI plus two small transport shims — not a rewrite.
The UI is now started and is built one verified feature at a time.

Two assets make both the remaining work and any future port cheap:

* `fixtures/*.jsonl` — recorded device traffic, byte-exact golden input;
* `tests/test_parity_tl_frame.py` — the reference behaviour, as an executable
  specification any port can be checked against.

### Constraints the app must honour

1. **Never expose the hearing / ear-scan commands** (`0x011E`, `0x011F`, `0x040E`,
   `0x0415`, capability bits 19/30) in the UI without a hard warning — they drive
   **loud test tones**. Only `0x0404 action=1` (noise mode) is safe.
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
| packaging | PyInstaller / Briefcase |
| Android | not in the first cut |

**Why this one.** It reuses the verified protocol verbatim. It keeps one language
across the reverse-engineering work and the app, so there is no second
implementation of the wire format to keep in sync. And the only code that does
not already exist is a UI plus two small transport shims — which is why the
first real release is about a week of work rather than a month.

**What it costs.** No Android in the first cut, and Qt/PyInstaller packaging is
the genuinely annoying part. Neither affects the core, which is where the value
is and which is why Android stays reachable later (§5).

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

Options B–F every one of them require porting `tlcore`. That port is ~600–700
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
